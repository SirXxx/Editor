from __future__ import annotations

from typing import List

from app.models.schemas import ExtractedFormula


class FormulaExtractor:
    """公式提取占位层。

    当前先保留统一接口；后续可接：
    - pix2tex
    - nougat
    - Mathpix / 其他公式 OCR API
    """

    def extract_from_images(self, image_paths: List[str], page: int) -> List[ExtractedFormula]:
        formulas: List[ExtractedFormula] = []
        for idx, _ in enumerate(image_paths, start=1):
            formulas.append(ExtractedFormula(page=page, index=idx, latex=None, raw_text=None))
        return formulas
