from __future__ import annotations

from typing import List

from app.models.schemas import DocumentExtractionResult


class MarkdownRestorer:
    """把抽取结果恢复成更适合 LLM 审核的 Markdown 文档。"""

    def to_markdown(self, result: DocumentExtractionResult) -> str:
        parts: List[str] = []
        parts.append(f"# 文档审校中间稿\n")
        parts.append(f"> 来源文件: {result.source_file}")
        parts.append(f"> 页数: {result.pages}\n")

        current_page = None
        for block in result.blocks:
            if current_page != block.page:
                current_page = block.page
                parts.append(f"\n## 第 {current_page} 页\n")
            if block.kind == 'title':
                parts.append(f"### {block.text}\n")
            else:
                parts.append(block.text + "\n")

        if result.tables:
            parts.append("\n## 表格提取\n")
            for idx, table in enumerate(result.tables, start=1):
                parts.append(f"### 表格 {idx}（第 {table.page} 页）\n")
                parts.append(table.raw_markdown or "[表格待进一步结构化解析]\n")

        if result.images:
            parts.append("\n## 图片提取\n")
            for img in result.images:
                parts.append(f"- 第 {img.page} 页 图片 {img.index}: `{img.path}`")
                if img.ocr_text:
                    parts.append(f"  - OCR: {img.ocr_text}")

        if result.formulas:
            parts.append("\n## 公式提取\n")
            for fm in result.formulas:
                parts.append(f"- 第 {fm.page} 页 公式 {fm.index}: {fm.latex or fm.raw_text or '[待识别]'}")

        return "\n".join(parts).strip() + "\n"
