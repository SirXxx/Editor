from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.models.schemas import KBChunk
from app.kb.vector_router import VectorStoreRouter
from app.kb.builder import KnowledgeBaseBuilder
from app.kb.web_loader import WebKnowledgeLoader


class MultiKnowledgeBaseManager:
    def __init__(self):
        self.stores: Dict[str, VectorStoreRouter] = defaultdict(VectorStoreRouter)

    def set_backend(self, backend: str, category: Optional[str] = None):
        if category:
            self.stores[category].set_backend(backend)
        else:
            for store in self.stores.values():
                store.set_backend(backend)

    def add_chunks(self, chunks: List[KBChunk], category: str = 'general'):
        self.stores[category].add_chunks(chunks)

    def clear_category(self, category: str):
        self.stores[category].clear()

    def list_categories(self) -> List[str]:
        return sorted(self.stores.keys())

    def search(self, query: str, top_k: int = 8, category: Optional[str] = None) -> List[KBChunk]:
        if category and category in self.stores:
            return self.stores[category].search(query, top_k=top_k)
        merged: List[KBChunk] = []
        for store in self.stores.values():
            merged.extend(store.search(query, top_k=top_k))
        seen = set()
        uniq: List[KBChunk] = []
        for c in merged:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                uniq.append(c)
        return uniq[:top_k]


class KnowledgeBaseManager(MultiKnowledgeBaseManager):
    """带持久化路径和配置的知识库管理器，供 API 层使用。"""

    def __init__(self, base_dir: str, config: Any):
        super().__init__()
        self.base_dir = base_dir
        self.config = config
        self._builder = KnowledgeBaseBuilder()
        self._web_loader = WebKnowledgeLoader()

    def rebuild_from_folder(self, folder: str, category: str = "general") -> int:
        """从本地文件夹重建指定类别的知识库，返回导入的 chunk 数量。"""
        chunks = self._builder.load_text_files(folder)
        self.clear_category(category)
        self.add_chunks(chunks, category)
        return len(chunks)

    def import_url(self, url: str, category: str = "general") -> dict:
        """从网页 URL 导入知识，返回导入摘要。"""
        chunks = self._web_loader.load_url(url)
        self.add_chunks(chunks, category)
        return {"url": url, "count": len(chunks), "category": category}
