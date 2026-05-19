from __future__ import annotations

from typing import List
import math

from app.models.schemas import KBChunk


class SimpleVectorStore:
    """轻量本地向量库占位实现。

    当前版本使用极简词频重叠评分，目的是先让系统跑通。
    后续可替换为：
    - FAISS
    - Qdrant
    - Milvus
    - pgvector
    """

    def __init__(self):
        self.chunks: List[KBChunk] = []

    def add_chunks(self, chunks: List[KBChunk]):
        self.chunks.extend(chunks)

    def search(self, query: str, top_k: int = 5) -> List[KBChunk]:
        q_terms = set(query.lower().split())
        scored = []
        for chunk in self.chunks:
            terms = set(chunk.text.lower().split())
            score = len(q_terms & terms) / (math.sqrt(len(terms) + 1))
            scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for s, c in scored[:top_k] if s > 0]
