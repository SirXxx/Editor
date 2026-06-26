"""两阶段并行审稿编排器。

Phase 1（快速扫描，~5-15s）：grammar / spelling / style 并行
Phase 2（深度分析，~15-40s）：terminology / logic / fact 并行

架构要点
---------
- 同一阶段内所有 (chunk × dimension) 任务同时提交给 ThreadPoolExecutor
- 每完成一个子任务立即通过 event_callback 推送事件（供 SSE 实时消费）
- cancel_check 在每次 future 完成时检查，快速中止循环（已提交的 LLM 请求
  会自然结束，不强制中断网络 IO）
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

from app.llm.providers import LLMProvider
from app.models.schemas import ReviewIssue, ReviewResult
from app.review.dimensions import (
    REVIEW_DIMENSIONS,
    PHASE1_DIMS,
    PHASE2_DIMS,
    DIM_TO_ISSUE_TYPE,
    VALID_ISSUE_TYPES,
)
from app.review.structured_parser import parse_review_json
from app.review.rules import get_rules_manager

_PY39 = sys.version_info >= (3, 9)


class ReviewOrchestrator:
    """两阶段并行审稿编排器。支持 Phase1/Phase2 使用不同 LLM（混合模型策略）。"""

    def __init__(
        self,
        llm: LLMProvider,
        kb_manager=None,
        config=None,
        max_workers: int = 3,
        chunk_size: int = 2000,
        llm_phase2: Optional[LLMProvider] = None,
    ):
        self.llm = llm                                    # Phase 1 provider
        self.llm_phase2 = llm_phase2 or llm              # Phase 2 provider (fallback to same)
        self.kb = kb_manager
        self.config = config
        self.max_workers = max_workers
        self.chunk_size = chunk_size

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def review_with_events(
        self,
        text: str,
        dimensions: Optional[List[str]] = None,
        kb_category: Optional[str] = None,
        event_callback: Optional[Callable] = None,
        cancel_check: Optional[Callable] = None,
    ) -> ReviewResult:
        """主入口：两阶段并行审稿，通过 event_callback 实时推送进度事件。

        Parameters
        ----------
        text:           待审稿的纯文本内容
        dimensions:     要启用的维度列表（默认全部六个）
        kb_category:    知识库类别（可选）
        event_callback: 事件回调 fn(event_type: str, **kwargs)，可抛异常以取消
        cancel_check:   轮询取消标志 fn() -> bool
        """
        dimensions = dimensions or list(REVIEW_DIMENSIONS.keys())
        chunks = self._split_text(text)
        total_chunks = len(chunks)

        def emit(event_type: str, **kwargs) -> None:
            if event_callback:
                event_callback(event_type, **kwargs)

        def cancelled() -> bool:
            return bool(cancel_check and cancel_check())

        emit("doc_parsed", total_chunks=total_chunks, text_length=len(text),
             message=f"文档解析完成，共 {total_chunks} 段，{len(text)} 字")

        all_issues: List[ReviewIssue] = []

        # ── Phase 1: 快速扫描 ─────────────────────────────────────────────────
        phase1_dims = [d for d in dimensions if d in PHASE1_DIMS]
        if phase1_dims and not cancelled():
            t0 = time.time()
            p1_model = getattr(self.llm, 'model', '')
            emit("phase_start", phase=1, dimensions=phase1_dims,
                 total_tasks=total_chunks * len(phase1_dims),
                 model=p1_model,
                 message=f"Phase 1  语法 · 格式 · 风格  扫描中…（{p1_model}）")
            issues1 = self._run_phase_parallel(
                chunks, phase1_dims, kb_category, emit, cancelled,
                llm=self.llm,
            )
            all_issues.extend(issues1)
            emit("phase_done", phase=1, issue_count=len(issues1),
                 elapsed_s=round(time.time() - t0, 1),
                 model=p1_model)

        # ── Phase 2: 深度分析 ─────────────────────────────────────────────────
        phase2_dims = [d for d in dimensions if d in PHASE2_DIMS]
        if phase2_dims and not cancelled():
            t0 = time.time()
            p2_model = getattr(self.llm_phase2, 'model', '')
            emit("phase_start", phase=2, dimensions=phase2_dims,
                 total_tasks=total_chunks * len(phase2_dims),
                 model=p2_model,
                 message=f"Phase 2  术语 · 逻辑 · 事实  深度分析中…（{p2_model}）")
            issues2 = self._run_phase_parallel(
                chunks, phase2_dims, kb_category, emit, cancelled,
                llm=self.llm_phase2,
            )
            all_issues.extend(issues2)
            emit("phase_done", phase=2, issue_count=len(issues2),
                 elapsed_s=round(time.time() - t0, 1),
                 model=p2_model)

        # ── 汇总 ──────────────────────────────────────────────────────────────
        summary = self._build_summary(all_issues)
        dim_stats = self._dimension_stats(all_issues)
        emit("completed",
             total_issues=len(all_issues),
             summary=summary,
             dimension_stats=dim_stats,
             issues=[i.model_dump() for i in all_issues])

        return ReviewResult(
            source_file="",
            summary=summary,
            revised_text="",
            issues=all_issues,
            references=[],
            dimension_stats=dim_stats,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _run_phase_parallel(
        self,
        chunks: List[str],
        dimensions: List[str],
        kb_category: Optional[str],
        emit: Callable,
        cancelled: Callable,
        llm: Optional[LLMProvider] = None,
    ) -> List[ReviewIssue]:
        """并行运行 chunks × dimensions 的全部子任务。llm 参数指定本阶段使用的 provider。"""
        active_llm = llm or self.llm
        tasks = [
            (chunk_idx, chunk, dim)
            for chunk_idx, chunk in enumerate(chunks)
            for dim in dimensions
        ]
        all_issues: List[ReviewIssue] = []
        done_count = 0
        total_tasks = len(tasks)

        pool = ThreadPoolExecutor(max_workers=self.max_workers)
        try:
            futures = {
                pool.submit(self._review_one, chunk, dim, kb_category, active_llm): (chunk_idx, dim)
                for chunk_idx, chunk, dim in tasks
            }
            for future in as_completed(futures):
                chunk_idx, dim = futures[future]
                if cancelled():
                    break
                done_count += 1
                try:
                    issues = future.result()
                except Exception as exc:
                    issues = []
                    emit("chunk_error", chunk=chunk_idx, dimension=dim,
                         error=str(exc))

                all_issues.extend(issues)
                if issues:
                    emit("chunk_done",
                         chunk=chunk_idx,
                         dimension=dim,
                         dim_name=REVIEW_DIMENSIONS[dim]["name"],
                         done=done_count,
                         total=total_tasks,
                         issues=[i.model_dump() for i in issues],
                         count=len(issues))
        finally:
            if _PY39:
                pool.shutdown(wait=False, cancel_futures=True)
            else:
                pool.shutdown(wait=False)

        return all_issues

    def _review_one(
        self,
        chunk_text: str,
        dimension: str,
        kb_category: Optional[str],
        llm: Optional[LLMProvider] = None,
    ) -> List[ReviewIssue]:
        """审稿单个 chunk 的单个维度，返回 ReviewIssue 列表。"""
        active_llm = llm or self.llm
        dim_cfg = REVIEW_DIMENSIONS[dimension]
        ref_text = self._search_refs(chunk_text, kb_category)

        # Build system prompt: base + user custom rules suffix
        rules_suffix = get_rules_manager().get_prompt_suffix(dimension)
        system_prompt = dim_cfg["system_prompt"] + rules_suffix

        user_msg = f"请审校以下内容：\n\n【文档片段】\n{chunk_text}"
        if ref_text and ref_text != "[无命中参考资料]":
            user_msg += f"\n\n【参考资料】\n{ref_text}"

        response = active_llm.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ])
        return self._parse_issues(response, dimension)

    def _parse_issues(self, response: str, dimension: str) -> List[ReviewIssue]:
        parsed = parse_review_json(response)
        issues: List[ReviewIssue] = []
        for item in parsed.get("issues", []):
            try:
                raw_type = item.get("issue_type", dimension)
                issue_type = (
                    raw_type if raw_type in VALID_ISSUE_TYPES
                    else DIM_TO_ISSUE_TYPE.get(dimension, "grammar")
                )
                original = str(item.get("original", "")).strip()
                if not original or len(original) < 2:
                    continue
                issue = ReviewIssue(
                    issue_type=issue_type,
                    severity=item.get("severity", "medium"),
                    original=original,
                    suggestion=str(item.get("suggestion", "")),
                    reason=str(item.get("reason", "")),
                    evidence=item.get("evidence"),
                    dimension=dimension,
                    confidence=min(1.0, max(0.0, float(item.get("confidence", 0.8)))),
                    needs_review=bool(item.get("needs_review", False)),
                )
                issues.append(issue)
            except Exception:
                continue
        return issues

    def _split_text(self, text: str) -> List[str]:
        """按段落分割文本为固定大小的 chunk（不切断段落）。"""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if not paragraphs:
            return [text] if text.strip() else [""]
        chunks, cur, cur_len = [], [], 0
        for para in paragraphs:
            if cur_len + len(para) > self.chunk_size and cur:
                chunks.append("\n".join(cur))
                cur, cur_len = [para], len(para)
            else:
                cur.append(para)
                cur_len += len(para)
        if cur:
            chunks.append("\n".join(cur))
        return chunks or [text]

    def _search_refs(self, query: str, kb_category: Optional[str]) -> str:
        if not self.kb:
            return ""
        try:
            refs = self.kb.search(query[:1200], top_k=5, category=kb_category)
        except TypeError:
            refs = self.kb.search(query[:1200], top_k=5)
        if not refs:
            return "[无命中参考资料]"
        return "\n\n".join(
            f"[参考{i + 1}] {r.text[:300]}" for i, r in enumerate(refs)
        )

    def _dimension_stats(self, issues: List[ReviewIssue]) -> Dict[str, int]:
        stats: Dict[str, int] = {}
        for issue in issues:
            dim = getattr(issue, "dimension", None) or issue.issue_type
            stats[dim] = stats.get(dim, 0) + 1
        return stats

    def _build_summary(self, issues: List[ReviewIssue]) -> str:
        if not issues:
            return "审稿完成，未发现明显问题。"
        stats = self._dimension_stats(issues)
        parts = []
        for dim, count in stats.items():
            name = REVIEW_DIMENSIONS.get(dim, {}).get("name", dim)
            parts.append(f"{name} {count} 处")
        high = sum(1 for i in issues if i.severity == "high")
        med = sum(1 for i in issues if i.severity == "medium")
        return (
            f"共发现 {len(issues)} 处问题"
            f"（严重 {high} 处 · 中等 {med} 处）：" + "、".join(parts)
        )
