from __future__ import annotations

from typing import List, Optional
import os

from app.models.schemas import KBChunk
from app.kb.real_embeddings import RealEmbeddingProvider


class QdrantVectorStore:
    """Qdrant 真实适配层（可选依赖 qdrant-client）。"""

    def __init__(self, collection_name: str = 'editor_kb', embedding_provider: str = 'simple', embedding_model: Optional[str] = None):
        self.collection_name = collection_name
        self.embedder = RealEmbeddingProvider(provider=embedding_provider, model=embedding_model)
        self._chunks: List[KBChunk] = []
        self.url = os.getenv('QDRANT_URL', '')
        self.api_key = os.getenv('QDRANT_API_KEY', '')
        self.client = None
        try:
            from qdrant_client import QdrantClient
            self.client = QdrantClient(url=self.url or None, api_key=self.api_key or None)
        except Exception:
            self.client = None

    def add_chunks(self, chunks: List[KBChunk]):
        if not chunks:
            return
        if self.client is None:
            self._chunks.extend(chunks)
            return
        vectors = self.embedder.embed([c.text for c in chunks])
        from qdrant_client.models import PointStruct, VectorParams, Distance
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            size = len(vectors[0]) if vectors else 64
            self.client.recreate_collection(self.collection_name, vectors_config=VectorParams(size=size, distance=Distance.COSINE))
        points = []
        for idx, (chunk, vec) in enumerate(zip(chunks, vectors), start=len(self._chunks) + 1):
            points.append(PointStruct(id=idx, vector=vec, payload={'chunk_id': chunk.chunk_id, 'source': chunk.source, 'text': chunk.text}))
        self.client.upsert(collection_name=self.collection_name, points=points)
        self._chunks.extend(chunks)

    def search(self, query: str, top_k: int = 5) -> List[KBChunk]:
        if self.client is None:
            q = set(query.lower().split())
            scored = []
            for c in self._chunks:
                s = len(q & set(c.text.lower().split()))
                scored.append((s, c))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [x[1] for x in scored[:top_k] if x[0] > 0]
        qv = self.embedder.embed([query])[0]
        hits = self.client.search(collection_name=self.collection_name, query_vector=qv, limit=top_k)
        results = []
        for h in hits:
            p = h.payload or {}
            results.append(KBChunk(chunk_id=p.get('chunk_id', ''), source=p.get('source', ''), text=p.get('text', '')))
        return results
