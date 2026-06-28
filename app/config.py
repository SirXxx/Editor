from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.paths import CONFIG_PATH

# ── Built-in preset definitions ───────────────────────────────────────────────
BUILTIN_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "intranet_deepseek_v3",
        "name": "内网 · DeepSeek V3.2（快速扫描）",
        "group": "内网",
        "provider": "openai_compatible",
        "base_url": "https://api.llm-incubator.automotive.cloud/prod/v0/llm",
        "model": "DeepSeek-V3.2",
        "extra_headers": '{"X-Application-Name": "ai-editor-review"}',
        "note": "速度最快，推荐用于 Phase 1",
        "recommended": True,
    },
    {
        "id": "intranet_deepseek_v4_pro",
        "name": "内网 · DeepSeek V4-Pro（深度推理）",
        "group": "内网",
        "provider": "openai_compatible",
        "base_url": "https://api.llm-incubator.automotive.cloud/prod/v0/llm",
        "model": "DeepSeek-V4-Pro",
        "extra_headers": '{"X-Application-Name": "ai-editor-review"}',
        "note": "推理能力最强，推荐用于 Phase 2",
        "recommended": True,
    },
    {
        "id": "intranet_deepseek_v4_flash",
        "name": "内网 · DeepSeek V4-Flash",
        "group": "内网",
        "provider": "openai_compatible",
        "base_url": "https://api.llm-incubator.automotive.cloud/prod/v0/llm",
        "model": "DeepSeek-V4-Flash",
        "extra_headers": '{"X-Application-Name": "ai-editor-review"}',
        "note": "V4 快速版",
        "recommended": False,
    },
    {
        "id": "intranet_claude_sonnet",
        "name": "内网 · Claude Sonnet 4.6（当前默认）",
        "group": "内网",
        "provider": "openai_compatible",
        "base_url": "https://api.llm-incubator.automotive.cloud/prod/v0/llm",
        "model": "claude-4-6-sonnet-v1:0",
        "extra_headers": '{"X-Application-Name": "ai-editor-review"}',
        "note": "综合质量最稳，JSON 输出最稳定",
        "recommended": False,
    },
    {
        "id": "intranet_claude_opus_47",
        "name": "内网 · Claude Opus 4.7（最强推理）",
        "group": "内网",
        "provider": "openai_compatible",
        "base_url": "https://api.llm-incubator.automotive.cloud/prod/v0/llm",
        "model": "claude-4-7-opus-v1:0",
        "extra_headers": '{"X-Application-Name": "ai-editor-review"}',
        "note": "质量最高，速度较慢",
        "recommended": False,
    },
    {
        "id": "intranet_claude_opus_48",
        "name": "内网 · Claude Opus 4.8",
        "group": "内网",
        "provider": "openai_compatible",
        "base_url": "https://api.llm-incubator.automotive.cloud/prod/v0/llm",
        "model": "claude-4-8-opus-v1:0",
        "extra_headers": '{"X-Application-Name": "ai-editor-review"}',
        "note": "最新 Opus 版本",
        "recommended": False,
    },
    {
        "id": "intranet_claude_haiku",
        "name": "内网 · Claude Haiku 4.5（最快）",
        "group": "内网",
        "provider": "openai_compatible",
        "base_url": "https://api.llm-incubator.automotive.cloud/prod/v0/llm",
        "model": "claude-4-5-haiku-v1:0",
        "extra_headers": '{"X-Application-Name": "ai-editor-review"}',
        "note": "速度最快，适合大批量初筛",
        "recommended": False,
    },
    {
        "id": "internet_deepseek_chat",
        "name": "公网 · DeepSeek V3（推荐外网）",
        "group": "公网",
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "extra_headers": "",
        "note": "外网首选，性价比最高",
        "recommended": True,
    },
    {
        "id": "internet_deepseek_reasoner",
        "name": "公网 · DeepSeek R1（推理模型）",
        "group": "公网",
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-reasoner",
        "extra_headers": "",
        "note": "逻辑/事实核查最强，适合 Phase 2",
        "recommended": True,
    },
    {
        "id": "siliconflow_deepseek_v3",
        "name": "硅基流动 · DeepSeek-V3（速度快/便宜）",
        "group": "公网",
        "provider": "openai_compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "extra_headers": "",
        "note": "国内可直连，性价比极高，适合 Phase 1",
        "recommended": True,
    },
    {
        "id": "siliconflow_deepseek_r1",
        "name": "硅基流动 · DeepSeek-R1（推理模型）",
        "group": "公网",
        "provider": "openai_compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-R1",
        "extra_headers": "",
        "note": "国内可直连，逻辑推理强，适合 Phase 2",
        "recommended": True,
    },
    {
        "id": "siliconflow_qwen3_32b",
        "name": "硅基流动 · Qwen3-32B（中文强）",
        "group": "公网",
        "provider": "openai_compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen3-32B",
        "extra_headers": "",
        "note": "中文理解最强，适合中文专业文档审稿",
        "recommended": False,
    },
]


@dataclass
class LLMSettings:
    provider: str = "openai_compatible"
    base_url: str = "https://api.llm-incubator.automotive.cloud/prod/v0/llm"
    api_key: str = ""
    model: str = "DeepSeek-V3.2"
    extra_headers: str = '{"X-Application-Name": "ai-editor-review"}'


@dataclass
class HybridLLMSettings:
    """两阶段模型配置：Phase1（快速扫描）和 Phase2（深度分析）各自独立配置。"""
    # Phase 1：语法/格式/风格 — 推荐快速模型
    phase1_provider: str = "openai_compatible"
    phase1_base_url: str = "https://api.llm-incubator.automotive.cloud/prod/v0/llm"
    phase1_api_key: str = ""
    phase1_model: str = "DeepSeek-V3.2"
    phase1_extra_headers: str = '{"X-Application-Name": "ai-editor-review"}'
    # Phase 2：术语/逻辑/事实 — 推荐推理模型
    phase2_provider: str = "openai_compatible"
    phase2_base_url: str = "https://api.llm-incubator.automotive.cloud/prod/v0/llm"
    phase2_api_key: str = ""
    phase2_model: str = "DeepSeek-V4-Pro"
    phase2_extra_headers: str = '{"X-Application-Name": "ai-editor-review"}'


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
    hybrid_llm: HybridLLMSettings = field(default_factory=HybridLLMSettings)
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
        # Strip legacy "enabled" key so old configs don't break new dataclass
        hybrid = {k: v for k, v in raw.get("hybrid_llm", {}).items() if k != "enabled"}
        emb = raw.get("embedding", {})
        review = raw.get("review", {})
        cfg.llm = LLMSettings(**{**asdict(cfg.llm), **llm})
        cfg.hybrid_llm = HybridLLMSettings(**{**asdict(cfg.hybrid_llm), **hybrid})
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

