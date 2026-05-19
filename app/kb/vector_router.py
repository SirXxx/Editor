from __future__ import annotations

from typing import Dict, List

from app.models.schemas import KBChunk
from app.kb.vector_store import SimpleVectorStore
from app.kb.faiss_store import FAISSLikeStore
from app.kb.qdrant_store import QdrantVectorStore


class VectorStoreRouter:
    def __init__(self):
        self.backends: Dict[str, object] = {
            'simple': SimpleVectorStore(),
            'faiss_like': FAISSLikeStore(),
            'qdrant': QdrantVectorStore(),
        }
        self.active_backend = 'faiss_like'

    def set_backend(self, name: str):
        if name in self.backends:
            self.active_backend = name

    def add_chunks(self, chunks: List[KBChunk]):
        self.backends[self.active_backend].add_chunks(chunks)

    def clear(self):
        self.backends[self.active_backend] = type(self.backends[self.active_backend])()

    def search(self, query: str, top_k: int = 8) -> List[KBChunk]:
        return self.backends[self.active_backend].search(query, top_k=top_k)
