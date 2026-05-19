from __future__ import annotations

from typing import Any, Optional

from app.llm.providers import LLMProvider
from app.models.schemas import ReviewResult, ReviewIssue
from app.review.structured_parser import parse_review_json


SYSTEM_PROMPT = """
你是一个专业中文编辑与审稿助手。
请严格输出 JSON，结构如下：
{
  "summary": "总体摘要",
  "revised_text": "修订后的全文或主要修订稿",
  "issues": [
    {
      "issue_type": "spelling|grammar|format|style|fact|terminology|logic|reference",
      "severity": "low|medium|high",
      "page": 1,
      "block_id": "p1_b1",
      "original": "原文",
      "suggestion": "建议修改",
      "reason": "原因",
      "evidence": "依据"
    }
  ],
  "references": [
    {"source": "来源", "text": "引用内容"}
  ]
}
要求：
1. 不要输出 JSON 之外的任何文字。
2. 对不确定专业结论标记为需人工复核，并在 reason 中说明。
3. 优先识别：错字、病句、术语、格式、事实错误。
""".strip()


class Reviewer:
    def __init__(
        self,
        llm: LLMProvider,
        kb: Any = None,
        config: Any = None,
        kb_manager: Any = None,
    ):
        self.llm = llm
        self.kb = kb_manager if kb_manager is not None else kb
        self.config = config

    def _search_refs(self, query: str, kb_category: Optional[str] = None) -> str:
        if self.kb is None:
            return "[无命中参考资料]"
        try:
            refs = self.kb.search(query[:1200], top_k=8, category=kb_category)
        except TypeError:
            refs = self.kb.search(query[:1200], top_k=8)
        if not refs:
            return "[无命中参考资料]"
        return "\n\n".join([f"[参考{i+1}] 来源:{r.source}\n{r.text}" for i, r in enumerate(refs)])

    def _call_llm(self, text: str, ref_text: str, source_file: str = "") -> ReviewResult:
        user_prompt = f"""请对以下文档进行专业审校，并仅输出 JSON。

【文档内容】
{text}

【参考资料】
{ref_text}
""".strip()
        content = self.llm.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ])
        parsed = parse_review_json(content)
        issues = []
        for item in parsed.get('issues', []):
            try:
                issues.append(ReviewIssue(**item))
            except Exception:
                continue
        return ReviewResult(
            source_file=source_file,
            summary=parsed.get('summary', ''),
            revised_text=parsed.get('revised_text', content),
            issues=issues,
            references=parsed.get('references', [])
        )

    def review_text(self, text: str, kb_category: Optional[str] = None) -> ReviewResult:
        """审稿纯文本内容。"""
        ref_text = self._search_refs(text, kb_category)
        return self._call_llm(text, ref_text)

    def review_pdf(self, file_path: str, kb_category: Optional[str] = None, scan_mode: bool = False) -> ReviewResult:
        """提取 PDF 文本后审稿。"""
        from app.extractors.pdf_extractor import PDFExtractor
        extractor = PDFExtractor()
        extraction = extractor.extract(file_path)
        text = "\n".join([b.text for b in extraction.blocks])
        result = self.review_text(text=text, kb_category=kb_category)
        result.source_file = file_path
        return result

    def review(self, markdown_text: str, source_file: str) -> ReviewResult:
        """兼容旧接口。"""
        result = self.review_text(text=markdown_text)
        result.source_file = source_file
        return result
