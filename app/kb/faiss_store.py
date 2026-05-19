from __future__ import annotations

from typing import List, Optional
import math

from app.models.schemas import KBChunk
from app.kb.real_embeddings import RealEmbeddingProvider


class FAISSLikeStore:
    """可切换到真实 embedding 的近似向量存储。

    若安装 faiss-cpu，可进一步替换为真实 FAISS 实现；
    当前版本先使用真实 embedding + Python 相似度检索。
    """

    def __init__(self, embedding_provider: str = 'sentence_transformers', embedding_model: Optional[str] = 'shibing624/text2vec-base-chinese'):
        self.embedder = RealEmbeddingProvider(provider=embedding_provider, model=embedding_model)
        self.chunks: List[KBChunk] = []
        self.vectors: List[List[float]] = []

    def add_chunks(self, chunks: List[KBChunk]):
        if not chunks:
            return
        self.chunks.extend(chunks)
        self.vectors.extend(self.embedder.embed([c.text for c in chunks]))

    def _cosine(self, a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)

    def search(self, query: str, top_k: int = 8) -> List[KBChunk]:
        qv = self.embedder.embed([query])[0]
        scored = []
        for vec, chunk in zip(self.vectors, self.chunks):
            scored.append((self._cosine(qv, vec), chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for s, c in scored[:top_k] if s > 0]
