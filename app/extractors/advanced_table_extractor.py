from __future__ import annotations

from pathlib import Path
from typing import List

from app.models.schemas import ExtractedTable


class AdvancedTableExtractor:
    """高级表格提取适配层。

    优先尝试：
    - camelot（文本型 PDF）
    - pdfplumber（补充规则）
    当前若依赖不可用则返回空。
    """

    def extract(self, pdf_path: str) -> List[ExtractedTable]:
        tables: List[ExtractedTable] = []
        try:
            import camelot  # type: ignore
            c_tables = camelot.read_pdf(pdf_path, pages='all', flavor='stream')
            for idx, tb in enumerate(c_tables, start=1):
                try:
                    md = tb.df.to_markdown(index=False)
                except Exception:
                    md = tb.df.to_csv(index=False)
                tables.append(ExtractedTable(page=idx, title=f'Camelot表格{idx}', raw_markdown=md))
        except Exception:
            pass
        return tables
