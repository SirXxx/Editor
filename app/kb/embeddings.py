from __future__ import annotations

from typing import List


class EmbeddingProvider:
    def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError


class SimpleHashEmbedding(EmbeddingProvider):
    """离线占位 embedding；便于无依赖跑通。"""

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * 64
            for token in text.split():
                vec[hash(token) % 64] += 1.0
            vectors.append(vec)
        return vectors
