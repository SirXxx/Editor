from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    from paddleocr import PaddleOCR
except Exception:  # pragma: no cover
    PaddleOCR = None


class OCREngine:
    """OCR 抽象层。

    默认优先 PaddleOCR（对中文较友好）。
    后续可以很容易替换为：
    - 第三方高精 OCR API
    - Tesseract
    - 云厂商 OCR
    """

    def __init__(self, use_angle_cls: bool = True, lang: str = "ch"):
        self.lang = lang
        self.engine = None
        if PaddleOCR is not None:
            self.engine = PaddleOCR(use_angle_cls=use_angle_cls, lang=lang)

    def available(self) -> bool:
        return self.engine is not None

    def recognize_image(self, image_path: str) -> str:
        path = Path(image_path)
        if not path.exists():
            return ""
        if self.engine is None:
            return ""
        result = self.engine.ocr(str(path), cls=True)
        lines = []
        for item in result:
            for line in item:
                try:
                    lines.append(line[1][0])
                except Exception:
                    continue
        return "\n".join(lines).strip()
