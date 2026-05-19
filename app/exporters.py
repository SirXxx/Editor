from __future__ import annotations

from pathlib import Path
from typing import Iterable
import csv
import json

from app.models.schemas import ReviewResult


class ReviewExporter:
    def export_markdown(self, result: ReviewResult, out_path: str):
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# 审稿报告\n", f"来源：{result.source_file}\n", "## 摘要\n", result.summary, "\n## 修订稿\n", result.revised_text, "\n## 问题列表\n"]
        for i, issue in enumerate(result.issues, start=1):
            lines.append(f"### 问题 {i}")
            lines.append(f"- 类型：{issue.issue_type}")
            lines.append(f"- 严重程度：{issue.severity}")
            lines.append(f"- 原文：{issue.original}")
            lines.append(f"- 建议：{issue.suggestion}")
            lines.append(f"- 原因：{issue.reason}")
            lines.append(f"- 依据：{issue.evidence or ''}\n")
        p.write_text("\n".join(lines), encoding='utf-8')

    def export_csv(self, result: ReviewResult, out_path: str):
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['issue_type', 'severity', 'page', 'block_id', 'original', 'suggestion', 'reason', 'evidence'])
            for it in result.issues:
                writer.writerow([it.issue_type, it.severity, it.page, it.block_id, it.original, it.suggestion, it.reason, it.evidence])

    def export_json(self, result: ReviewResult, out_path: str):
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(result.model_dump_json(indent=2), encoding='utf-8')



def export_review_result(result: ReviewResult, file_path: str, mode: str = "markdown"):
    exporter = ReviewExporter()
    mode = (mode or "markdown").lower()
    if mode in {"markdown", "md"}:
        return exporter.export_markdown(result, file_path)
    if mode == "csv":
        return exporter.export_csv(result, file_path)
    if mode == "json":
        return exporter.export_json(result, file_path)
    raise ValueError(f"Unsupported export mode: {mode}")
