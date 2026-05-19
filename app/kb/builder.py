from __future__ import annotations

from pathlib import Path
from typing import List
import hashlib

from app.models.schemas import KBChunk


class KnowledgeBaseBuilder:
    def chunk_text(self, text: str, source: str, chunk_size: int = 800) -> List[KBChunk]:
        text = text.strip()
        if not text:
            return []
        chunks: List[KBChunk] = []
        start = 0
        idx = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            piece = text[start:end].strip()
            if piece:
                chunk_id = hashlib.md5(f"{source}:{idx}:{piece[:50]}".encode("utf-8")).hexdigest()
                chunks.append(KBChunk(chunk_id=chunk_id, source=source, text=piece))
                idx += 1
            start = end
        return chunks

    def load_text_files(self, root: str) -> List[KBChunk]:
        base = Path(root)
        all_chunks: List[KBChunk] = []
        for path in base.rglob('*'):
            if path.is_file() and path.suffix.lower() in {'.txt', '.md'}:
                text = path.read_text(encoding='utf-8', errors='ignore')
                all_chunks.extend(self.chunk_text(text, str(path)))
        return all_chunks
