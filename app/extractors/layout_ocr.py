from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any

import fitz

from app.extractors.ocr_engine import OCREngine


class LayoutOCRProcessor:
    """扫描件 / 图片型 PDF 的页级 OCR 与简易版面恢复。

    策略：
    1. 将 PDF 每页渲染为高分辨率图片
    2. 调用 OCR
    3. 生成页级文本与图片路径

    后续可升级为真正版面分析（标题/段落/表格/图注区域检测）。
    """

    def __init__(self, dpi: int = 220):
        self.dpi = dpi
        self.ocr = OCREngine()

    def pdf_to_page_images(self, pdf_path: str, out_dir: str) -> List[str]:
        doc = fitz.open(pdf_path)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        image_paths: List[str] = []
        zoom = self.dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            path = out / f"page_{i:04d}.png"
            pix.save(str(path))
            image_paths.append(str(path))
        doc.close()
        return image_paths

    def extract_pdf_pages(self, pdf_path: str, out_dir: str) -> List[Dict[str, Any]]:
        page_images = self.pdf_to_page_images(pdf_path, out_dir)
        results = []
        for i, img in enumerate(page_images, start=1):
            text = self.ocr.recognize_image(img)
            results.append({"page": i, "image": img, "text": text})
        return results
