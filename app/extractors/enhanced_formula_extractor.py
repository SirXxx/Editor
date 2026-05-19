from __future__ import annotations

from typing import List

from app.models.schemas import ExtractedFormula


class EnhancedFormulaExtractor:
    """增强公式提取占位层。

    可接：
    - pix2tex
    - Mathpix API
    - nougat
    当前返回空结果或占位，保持接口完整。
    """

    def extract_from_image_paths(self, image_paths: List[str], page: int) -> List[ExtractedFormula]:
        return [ExtractedFormula(page=page, index=i + 1, latex=None, raw_text='[公式识别待接入]') for i, _ in enumerate(image_paths)]
