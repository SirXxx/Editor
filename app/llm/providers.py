from __future__ import annotations

import os
from typing import Optional, List, Dict

import requests

from app.config import load_config


class LLMProvider:
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None, extra_headers: Optional[str] = None):
        cfg = load_config().get('llm', {})
        self.base_url = (base_url or os.getenv('LLM_BASE_URL') or cfg.get('base_url', '')).rstrip('/')
        self.api_key = api_key or os.getenv('LLM_API_KEY') or cfg.get('api_key', '')
        self.model = model or os.getenv('LLM_MODEL') or cfg.get('model', 'gpt-4o-mini')
        self.extra_headers: Dict[str, str] = {}
        raw = extra_headers or cfg.get('extra_headers', '')
        if raw:
            try:
                import json as _json
                self.extra_headers = _json.loads(raw)
            except Exception:
                pass

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            **self.extra_headers,
        }
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature,
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content']


class MockProvider(LLMProvider):
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        # 模拟模式：不读取文档，仅用于测试流程
        import json
        return json.dumps({
            "summary": "⚠️ 当前使用的是【模拟模式】，并未对文档做任何分析。\n请到 [① 配置模型] 页面设置真实的 LLM（如 OpenAI / DeepSeek / Ollama）再重新审稿。",
            "revised_text": "【模拟模式未生成任何内容】",
            "issues": [],
            "references": []
        }, ensure_ascii=False)


class OllamaCompatibleProvider(OpenAICompatibleProvider):
    pass


def build_provider(provider: str = 'mock') -> LLMProvider:
    if provider == 'openai_compatible':
        return OpenAICompatibleProvider()
    if provider == 'ollama_compatible':
        return OllamaCompatibleProvider()
    return MockProvider()


class LLMProviderFactory:
    @classmethod
    def from_config(cls, config: object) -> LLMProvider:
        llm_cfg = getattr(config, 'llm', None)
        if llm_cfg is None:
            return MockProvider()
        provider = getattr(llm_cfg, 'provider', 'mock')
        base_url = getattr(llm_cfg, 'base_url', '')
        api_key = getattr(llm_cfg, 'api_key', '')
        model = getattr(llm_cfg, 'model', '')
        if provider == 'openai_compatible':
            return OpenAICompatibleProvider(base_url=base_url, api_key=api_key, model=model, extra_headers=getattr(llm_cfg, 'extra_headers', ''))
        if provider == 'ollama_compatible':
            return OllamaCompatibleProvider(base_url=base_url, api_key=api_key, model=model, extra_headers=getattr(llm_cfg, 'extra_headers', ''))
        return MockProvider()
