from __future__ import annotations

from typing import List

from app.models.schemas import ExtractedTable


class TableExtractor:
    """表格抽取占位层。

    当前版本保留接口，后续可替换为：
    - pdfplumber 精细表格解析
    - camelot
    - tabula-py
    - PaddleOCR + 版面分析
    """

    def refine_tables(self, tables: List[ExtractedTable]) -> List[ExtractedTable]:
        return tables
