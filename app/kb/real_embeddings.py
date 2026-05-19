from __future__ import annotations

from typing import List, Optional
import os
import requests


class RealEmbeddingProvider:
    """真实 embedding 提供者。

    支持：
    - simple（退化模式）
    - sentence_transformers（若本地安装）
    - openai_compatible_embeddings（兼容 embedding 接口）
    """

    def __init__(self, provider: str = 'simple', model: Optional[str] = None, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.provider = provider
        self.model = model or os.getenv('EMBEDDING_MODEL', 'text-embedding-3-small')
        self.base_url = (base_url or os.getenv('EMBEDDING_BASE_URL', '')).rstrip('/')
        self.api_key = api_key or os.getenv('EMBEDDING_API_KEY', '')
        self._st_model = None
        if provider == 'sentence_transformers':
            try:
                from sentence_transformers import SentenceTransformer
                self._st_model = SentenceTransformer(self.model)
            except Exception:
                self._st_model = None

    def embed(self, texts: List[str]) -> List[List[float]]:
        if self.provider == 'sentence_transformers' and self._st_model is not None:
            vecs = self._st_model.encode(texts, normalize_embeddings=True)
            return [list(map(float, v)) for v in vecs]
        if self.provider == 'openai_compatible_embeddings' and self.base_url:
            return [self._embed_openai_compatible(t) for t in texts]
        from app.kb.embeddings import SimpleHashEmbedding
        return SimpleHashEmbedding().embed(texts)

    def _embed_openai_compatible(self, text: str) -> List[float]:
        url = f"{self.base_url}/embeddings"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        payload = {'model': self.model, 'input': text}
        resp = requests.post(url, json=payload, headers=headers, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        return data['data'][0]['embedding']
