from __future__ import annotations

import argparse
from pathlib import Path

from app.extractors.pdf_extractor import PDFExtractor
from app.extractors.ocr_engine import OCREngine
from app.extractors.layout_ocr import LayoutOCRProcessor
from app.extractors.advanced_table_extractor import AdvancedTableExtractor
from app.normalizers.markdown_restorer import MarkdownRestorer
from app.kb.builder import KnowledgeBaseBuilder
from app.kb.vector_store import SimpleVectorStore
from app.kb.web_loader import WebKnowledgeLoader
from app.llm.providers import build_provider
from app.review.reviewer import Reviewer


def cli():
    parser = argparse.ArgumentParser(description='AI 审稿系统 CLI')
    parser.add_argument('pdf', nargs='?', help='待审校 PDF 路径')
    parser.add_argument('--rebuild-kb', action='store_true', help='重建 data/kb 下的知识库索引')
    parser.add_argument('--import-url', help='导入网页到知识库')
    parser.add_argument('--provider', default='mock', choices=['mock', 'openai_compatible'])
    parser.add_argument('--scan-mode', action='store_true', help='扫描件模式')
    args = parser.parse_args()

    kb_builder = KnowledgeBaseBuilder()
    store = SimpleVectorStore()
    web_loader = WebKnowledgeLoader()

    if args.rebuild_kb:
        chunks = kb_builder.load_text_files('data/kb')
        store.add_chunks(chunks)
        print(f'知识库已重建，共 {len(chunks)} 个 chunks')
    if args.import_url:
        chunks = web_loader.load_url(args.import_url)
        store.add_chunks(chunks)
        print(f'已导入网页知识库，共 {len(chunks)} 个 chunks')
        if not args.pdf:
            return

    if not args.pdf:
        print('请提供 PDF 路径，或使用 --rebuild-kb / --import-url')
        return

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f'文件不存在: {pdf_path}')
        return

    extractor = PDFExtractor()
    adv_tables = AdvancedTableExtractor()
    ocr = OCREngine()
    layout = LayoutOCRProcessor()
    restorer = MarkdownRestorer()

    extracted = extractor.extract(str(pdf_path))
    extracted.tables.extend(adv_tables.extract(str(pdf_path)))
    if args.scan_mode:
        pages = layout.extract_pdf_pages(str(pdf_path), str(Path('data/workspace') / f'{pdf_path.stem}_pages'))
        extracted.warnings.append(f'扫描件模式完成，共识别页数: {len(pages)}')
    for img in extracted.images:
        img.ocr_text = ocr.recognize_image(img.path)
    markdown = restorer.to_markdown(extracted)

    provider = build_provider(args.provider)
    reviewer = Reviewer(provider, store)
    result = reviewer.review(markdown, str(pdf_path))

    out_dir = Path('data/output')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f'{pdf_path.stem}_markdown.md').write_text(markdown, encoding='utf-8')
    (out_dir / f'{pdf_path.stem}_review.json').write_text(result.model_dump_json(indent=2), encoding='utf-8')
    print(f'审校完成，输出目录: {out_dir}')


if __name__ == '__main__':
    cli()
