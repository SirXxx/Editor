from __future__ import annotations

from pathlib import Path
from typing import List

import fitz  # PyMuPDF

from app.models.schemas import DocumentExtractionResult, ParagraphBlock, ExtractedImage, ExtractedTable


class PDFExtractor:
    """基础 PDF 提取器。

    当前版本优先支持：
    1. 文本型 PDF 的结构化提取
    2. 图片导出
    3. 粗粒度表格占位识别（后续可替换为 camelot / tabula / paddle）

    对扫描件 OCR、公式识别使用独立模块补强。
    """

    def extract(self, pdf_path: str) -> DocumentExtractionResult:
        pdf_file = Path(pdf_path)
        doc = fitz.open(pdf_file)
        result = DocumentExtractionResult(source_file=str(pdf_file), pages=len(doc))

        for page_index, page in enumerate(doc, start=1):
            blocks = page.get_text("dict").get("blocks", [])
            block_num = 0
            for b in blocks:
                if b.get("type") == 0:
                    lines: List[str] = []
                    for line in b.get("lines", []):
                        spans = line.get("spans", [])
                        line_text = "".join(span.get("text", "") for span in spans).strip()
                        if line_text:
                            lines.append(line_text)
                    text = "\n".join(lines).strip()
                    if text:
                        kind = 'paragraph'
                        if len(text) <= 40 and any(ch.isdigit() for ch in text[:8]):
                            kind = 'title'
                        result.blocks.append(
                            ParagraphBlock(
                                page=page_index,
                                block_id=f"p{page_index}_b{block_num}",
                                text=text,
                                kind=kind,
                                meta={"bbox": b.get("bbox")},
                            )
                        )
                        block_num += 1
                elif b.get("type") == 1:
                    # 图片块，先记录，稍后统一导出图片
                    pass

            # 导出图片
            for img_index, img in enumerate(page.get_images(full=True), start=1):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                img_dir = pdf_file.parent / "_extracted_images"
                img_dir.mkdir(exist_ok=True)
                img_path = img_dir / f"{pdf_file.stem}_p{page_index}_{img_index}.png"
                if pix.n - pix.alpha < 4:
                    pix.save(str(img_path))
                else:
                    rgb_pix = fitz.Pixmap(fitz.csRGB, pix)
                    rgb_pix.save(str(img_path))
                    rgb_pix = None
                pix = None
                result.images.append(ExtractedImage(page=page_index, index=img_index, path=str(img_path)))

            # 表格识别占位：后续可替换为专门表格引擎
            text = page.get_text().strip()
            if "\t" in text or ("表" in text and "|" in text):
                result.tables.append(ExtractedTable(page=page_index, title="候选表格", raw_markdown=text))

        doc.close()
        return result
