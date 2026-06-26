from __future__ import annotations

from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field


class TableCell(BaseModel):
    row: int
    col: int
    text: str = ""


class ExtractedTable(BaseModel):
    page: int
    title: Optional[str] = None
    cells: List[TableCell] = Field(default_factory=list)
    raw_markdown: Optional[str] = None


class ExtractedImage(BaseModel):
    page: int
    index: int
    path: str
    caption: Optional[str] = None
    ocr_text: Optional[str] = None


class ExtractedFormula(BaseModel):
    page: int
    index: int
    latex: Optional[str] = None
    raw_text: Optional[str] = None


class ParagraphBlock(BaseModel):
    page: int
    block_id: str
    section: Optional[str] = None
    text: str
    kind: Literal['paragraph', 'title', 'table', 'image', 'formula', 'list', 'reference'] = 'paragraph'
    meta: Dict[str, Any] = Field(default_factory=dict)


class DocumentExtractionResult(BaseModel):
    source_file: str
    pages: int = 0
    blocks: List[ParagraphBlock] = Field(default_factory=list)
    tables: List[ExtractedTable] = Field(default_factory=list)
    images: List[ExtractedImage] = Field(default_factory=list)
    formulas: List[ExtractedFormula] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class KBChunk(BaseModel):
    chunk_id: str
    source: str
    text: str
    section: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class ReviewIssue(BaseModel):
    issue_type: Literal['spelling', 'grammar', 'format', 'style', 'fact', 'terminology', 'logic', 'reference']
    severity: Literal['low', 'medium', 'high'] = 'medium'
    page: Optional[int] = None
    block_id: Optional[str] = None
    original: str
    suggestion: str
    reason: str
    evidence: Optional[str] = None
    # Extended fields for multi-dimension engine
    dimension: Optional[str] = None        # which review dimension produced this issue
    confidence: float = 0.8               # LLM confidence score 0–1
    needs_review: bool = False            # flag for human follow-up
    chunk_index: Optional[int] = None     # which text chunk this came from


class ReviewResult(BaseModel):
    source_file: str
    summary: str
    revised_text: str
    issues: List[ReviewIssue] = Field(default_factory=list)
    references: List[Dict[str, Any]] = Field(default_factory=list)
    # Extended fields for multi-dimension engine
    dimension_stats: Dict[str, int] = Field(default_factory=dict)
    source_text: Optional[str] = None     # extracted plain text for frontend display
