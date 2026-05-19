from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path("data/workspace/app_config.json")


@dataclass
class LLMSettings:
    provider: str = "mock"
    base_url: str = "http://localhost:8000/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    extra_headers: str = ""  # JSON 字符串，如 {"X-Application-Name": "myapp"}


@dataclass
class EmbeddingSettings:
    provider: str = "sentence_transformers"
    base_url: str = ""
    api_key: str = ""
    model: str = "shibing624/text2vec-base-chinese"


@dataclass
class ReviewSettings:
    default_category: str = "editor"


@dataclass
class AppConfig:
    llm: LLMSettings = field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    review: ReviewSettings = field(default_factory=ReviewSettings)
    vector_backends: Dict[str, str] = field(
        default_factory=lambda: {
            "general": "simple",
            "editor": "simple",
            "law": "simple",
            "biology": "simple",
            "chemistry": "simple",
            "physics": "simple",
        }
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return cls()

        cfg = cls()
        llm = raw.get("llm", {})
        emb = raw.get("embedding", {})
        review = raw.get("review", {})
        cfg.llm = LLMSettings(**{**asdict(cfg.llm), **llm})
        cfg.embedding = EmbeddingSettings(**{**asdict(cfg.embedding), **emb})
        cfg.review = ReviewSettings(**{**asdict(cfg.review), **review})
        cfg.vector_backends = {**cfg.vector_backends, **raw.get("vector_backends", {})}
        return cfg


def load_config() -> Dict[str, Any]:
    return AppConfig.load().to_dict()


def save_config(cfg: Dict[str, Any]) -> None:
    current = AppConfig.load()
    current.llm = LLMSettings(**{**asdict(current.llm), **cfg.get("llm", {})})
    current.embedding = EmbeddingSettings(**{**asdict(current.embedding), **cfg.get("embedding", {})})
    current.review = ReviewSettings(**{**asdict(current.review), **cfg.get("review", {})})
    current.vector_backends = {**current.vector_backends, **cfg.get("vector_backends", {})}
    current.save()
