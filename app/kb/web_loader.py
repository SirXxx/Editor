from __future__ import annotations

from typing import List
import requests
from bs4 import BeautifulSoup

from app.models.schemas import KBChunk
from app.kb.builder import KnowledgeBaseBuilder


class WebKnowledgeLoader:
    def __init__(self):
        self.builder = KnowledgeBaseBuilder()

    def load_url(self, url: str) -> List[KBChunk]:
        resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text('\n', strip=True)
        return self.builder.chunk_text(text, source=url)
