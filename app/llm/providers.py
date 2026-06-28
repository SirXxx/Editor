from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Optional, List, Dict

import requests

from app.config import load_config


# ── 全局 LLM 并发限流（A6）────────────────────────────────────────────────────
# 编排器对 (chunk × dimension) 并行提交，叠加两阶段后实际并发可能远超单阶段
# max_workers，容易触发服务端 429。这里用进程级信号量给所有 LLM 请求设全局上限。
_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "4") or "4")
_LLM_SEMAPHORE = threading.Semaphore(max(1, _MAX_CONCURRENCY))

# 可重试的 HTTP 状态码（限流与服务端临时故障）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3") or "3")


class LLMConfigError(Exception):
    """LLM 配置或连接错误，携带用户友好的诊断信息。"""
    def __init__(self, message: str, diagnosis: str = "", suggestion: str = ""):
        super().__init__(message)
        self.message = message
        self.diagnosis = diagnosis
        self.suggestion = suggestion

    def to_dict(self) -> dict:
        return {
            "error": "config_error",
            "message": self.message,
            "diagnosis": self.diagnosis,
            "suggestion": self.suggestion,
        }


# ── 已知公开服务的 Base URL 特征 → 友好名称 & 文档地址 ────────────────────────
_KNOWN_PROVIDERS = [
    # (url_pattern, display_name, key_guide, key_url)
    (r"api\.openai\.com",      "OpenAI",
     "前往 https://platform.openai.com/api-keys 生成 Key，格式为 sk-..."),
    (r"api\.deepseek\.com",    "DeepSeek",
     "前往 https://platform.deepseek.com/ 申请 Key，模型名填 deepseek-chat 或 deepseek-reasoner"),
    (r"api\.anthropic\.com",   "Anthropic (Claude)",
     "前往 https://console.anthropic.com/ 生成 Key，Base URL 应为 https://api.anthropic.com/v1"),
    (r"dashscope\.aliyuncs\.com|qwen", "阿里云 DashScope (Qwen)",
     "前往 https://dashscope.console.aliyun.com/ 开通并获取 API Key，模型名如 qwen-max"),
    (r"api\.moonshot\.cn",     "Moonshot (Kimi)",
     "前往 https://platform.moonshot.cn/ 获取 Key，模型名如 moonshot-v1-8k"),
    (r"api\.zhipuai\.cn|open\.bigmodel", "智谱 AI (GLM)",
     "前往 https://open.bigmodel.cn/ 获取 Key，模型名如 glm-4"),
    (r"api\.lingyiwanwu\.com|api\.01\.ai", "零一万物 (Yi)",
     "前往 https://platform.lingyiwanwu.com/ 获取 Key"),
    (r"api\.mistral\.ai",      "Mistral AI",
     "前往 https://console.mistral.ai/ 获取 Key，模型名如 mistral-large-latest"),
    (r"generativelanguage\.googleapis\.com|gemini", "Google Gemini",
     "前往 https://aistudio.google.com/app/apikey 获取 Key"),
    (r"localhost|127\.0\.0\.1", "本地服务 (Ollama / LM Studio)",
     "确认本地服务已启动。Ollama 默认端口 11434，Base URL 填 http://localhost:11434/v1"),
    (r"ollama",                 "Ollama",
     "确认 Ollama 已启动（ollama serve），模型名填已下载的模型如 qwen2.5:14b"),
]


def _detect_provider(base_url: str) -> Optional[str]:
    """根据 base_url 识别服务商，返回友好提示字符串。"""
    url_lower = base_url.lower()
    for pattern, name, guide in _KNOWN_PROVIDERS:
        if re.search(pattern, url_lower):
            return f"{name}：{guide}"
    return None


def _extract_body(resp: requests.Response) -> str:
    """从响应中提取最有用的错误文本。"""
    try:
        j = resp.json()
        # OpenAI / DeepSeek / most compatible APIs
        msg = (j.get("message")
               or j.get("error", {}).get("message", "")
               or j.get("detail", "")
               or j.get("msg", ""))
        return str(msg)[:300] if msg else resp.text[:300]
    except Exception:
        return resp.text[:300]


def _diagnose_http_error(resp: requests.Response, base_url: str = "") -> LLMConfigError:
    """将 HTTP 错误转化为用户可读的诊断信息，覆盖主流公开模型服务。"""
    code = resp.status_code
    body = _extract_body(resp)
    body_lower = body.lower()
    provider_hint = _detect_provider(base_url) if base_url else None

    # ── 401 Unauthorized ──────────────────────────────────────────────────────
    if code == 401:
        # Token expired (企业内网 / JWT 类系统)
        if "expired" in body_lower or "token has expired" in body_lower:
            return LLMConfigError(
                message="API Token 已过期",
                diagnosis=f"服务器返回 401：{body}",
                suggestion="请重新获取 API Key 并在配置页面更新。" + (f"\n{provider_hint}" if provider_hint else ""),
            )
        # OpenAI / DeepSeek style: invalid key
        if "incorrect api key" in body_lower or "invalid_api_key" in body_lower:
            return LLMConfigError(
                message="API Key 格式不正确",
                diagnosis=f"服务器返回 401：{body}",
                suggestion="OpenAI Key 格式为 sk-...，DeepSeek Key 格式为 sk-...，请检查是否完整复制。",
            )
        # Anthropic: x-api-key header missing
        if "x-api-key" in body_lower or "authentication_error" in body_lower:
            return LLMConfigError(
                message="Anthropic API Key 认证失败",
                diagnosis=f"服务器返回 401：{body}",
                suggestion="Anthropic 需要在附加请求头中填写 {\"x-api-key\": \"your-key\"}，同时 API Key 字段也需填写。",
            )
        return LLMConfigError(
            message="API Key 无效或未授权",
            diagnosis=f"服务器返回 401：{body}",
            suggestion=("请检查 API Key 是否正确，注意前后不要有多余空格或换行。"
                        + (f"\n{provider_hint}" if provider_hint else "")),
        )

    # ── 403 Forbidden ─────────────────────────────────────────────────────────
    if code == 403:
        if "header" in body_lower or "application" in body_lower or "x-application" in body_lower:
            return LLMConfigError(
                message="缺少必要的请求头（企业内网服务要求）",
                diagnosis=f"服务器返回 403：{body}",
                suggestion='在「附加请求头」中填入所需 Header，例如 {"X-Application-Name": "your-app-name"}',
            )
        if "quota" in body_lower or "insufficient" in body_lower:
            return LLMConfigError(
                message="账户配额不足或欠费",
                diagnosis=f"服务器返回 403：{body}",
                suggestion="请登录服务商控制台检查账户余额或配额。" + (f"\n{provider_hint}" if provider_hint else ""),
            )
        if "permission" in body_lower or "not allowed" in body_lower:
            return LLMConfigError(
                message="API Key 没有访问该模型的权限",
                diagnosis=f"服务器返回 403：{body}",
                suggestion="请检查 Key 是否有权限访问指定模型，部分模型（如 GPT-4）需要单独申请开通。",
            )
        return LLMConfigError(
            message="访问被拒绝（403 Forbidden）",
            diagnosis=f"服务器返回 403：{body}",
            suggestion="请检查 API Key 权限。企业内网服务通常还需要在附加请求头中添加认证信息。",
        )

    # ── 404 Not Found ─────────────────────────────────────────────────────────
    if code == 404:
        if "model" in body_lower and ("not found" in body_lower or "does not exist" in body_lower):
            return LLMConfigError(
                message="指定的模型不存在",
                diagnosis=f"服务器返回 404：{body}",
                suggestion=("请检查模型名称是否正确。\n"
                            "常见模型名：OpenAI → gpt-4o-mini | DeepSeek → deepseek-chat | "
                            "Qwen → qwen-max | Kimi → moonshot-v1-8k\n"
                            + (provider_hint or "")),
            )
        return LLMConfigError(
            message="接口地址不存在（404）",
            diagnosis=f"URL: {resp.url}",
            suggestion=("请检查 Base URL 格式。标准格式通常为：\n"
                        "• OpenAI:   https://api.openai.com/v1\n"
                        "• DeepSeek: https://api.deepseek.com/v1\n"
                        "• Ollama:   http://localhost:11434/v1\n"
                        "注意结尾不要加 /chat/completions，系统会自动拼接。"),
        )

    # ── 422 Unprocessable Entity ──────────────────────────────────────────────
    if code == 422:
        return LLMConfigError(
            message="请求参数格式错误（422）",
            diagnosis=f"服务器返回 422：{body}",
            suggestion="请检查模型名称是否与该服务商支持的格式匹配。",
        )

    # ── 429 Rate Limit ────────────────────────────────────────────────────────
    if code == 429:
        if "insufficient" in body_lower or "balance" in body_lower or "credit" in body_lower:
            return LLMConfigError(
                message="账户余额不足",
                diagnosis=f"服务器返回 429：{body}",
                suggestion="请登录服务商控制台充值后重试。" + (f"\n{provider_hint}" if provider_hint else ""),
            )
        return LLMConfigError(
            message="请求频率超限（429 Rate Limit）",
            diagnosis=f"服务器返回 429：{body}",
            suggestion="请稍等几秒后重试。若持续出现，请检查账户 RPM/TPM 配额限制。",
        )

    # ── 5xx Server Error ──────────────────────────────────────────────────────
    if code == 500:
        return LLMConfigError(
            message="LLM 服务内部错误（500）",
            diagnosis=f"服务器返回 500：{body}",
            suggestion="服务端异常，请稍后重试。若持续出现，请检查服务状态页。",
        )
    if code == 502 or code == 503:
        return LLMConfigError(
            message=f"LLM 服务暂时不可用（{code}）",
            diagnosis=f"服务器返回 {code}",
            suggestion="服务可能正在重启或过载，请稍后重试（通常 30-60 秒内恢复）。",
        )
    if code >= 500:
        return LLMConfigError(
            message=f"服务端错误（{code}）",
            diagnosis=f"服务器返回 {code}：{body}",
            suggestion="LLM 服务暂时不可用，请稍后重试。",
        )

    return LLMConfigError(
        message=f"请求失败（HTTP {code}）",
        diagnosis=body,
        suggestion="请检查网络连接和配置参数。",
    )


class LLMProvider:
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2,
             max_tokens: int = 4096) -> str:
        raise NotImplementedError

    def validate(self) -> dict:
        """验证配置是否可用。返回 {"ok": True} 或 {"ok": False, ...}"""
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, extra_headers: Optional[str] = None):
        cfg = load_config().get('llm', {})
        self.base_url = (base_url or os.getenv('LLM_BASE_URL') or cfg.get('base_url', '')).rstrip('/')
        self.api_key = api_key or os.getenv('LLM_API_KEY') or cfg.get('api_key', '')
        self.model = model or os.getenv('LLM_MODEL') or cfg.get('model', 'gpt-4o-mini')
        self.extra_headers: Dict[str, str] = {}
        raw = extra_headers or cfg.get('extra_headers', '')
        if raw:
            try:
                self.extra_headers = json.loads(raw)
            except Exception:
                pass

    def _build_headers(self) -> Dict[str, str]:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            **self.extra_headers,
        }

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2,
             max_tokens: int = 4096) -> str:
        if not self.base_url:
            raise LLMConfigError(
                message="未配置 Base URL",
                diagnosis="base_url 为空",
                suggestion="请在配置页面填写 LLM 接口地址，如 https://api.openai.com/v1",
            )
        if not self.api_key:
            raise LLMConfigError(
                message="未配置 API Key",
                diagnosis="api_key 为空",
                suggestion="请在配置页面填写 API Key。",
            )
        url = f"{self.base_url}/chat/completions"
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,   # A3: 显式上限，避免长输出被无声截断
        }
        last_exc: Optional[Exception] = None

        # A2: 对限流/5xx/超时/连接错误做指数退避重试；配置类错误（401/403/404）不重试。
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with _LLM_SEMAPHORE:   # A6: 全局并发上限
                    resp = requests.post(url, json=payload,
                                         headers=self._build_headers(), timeout=180)
            except requests.exceptions.ConnectionError as e:
                last_exc = LLMConfigError(
                    message="无法连接到 LLM 服务",
                    diagnosis=str(e)[:200],
                    suggestion=f"请检查 Base URL 是否可访问：{self.base_url}，以及网络连接是否正常。",
                )
            except requests.exceptions.Timeout:
                last_exc = LLMConfigError(
                    message="连接 LLM 服务超时（180s）",
                    diagnosis="请求超时",
                    suggestion="服务响应过慢，可尝试更换模型或检查服务状态。",
                )
            else:
                if resp.ok:
                    data = resp.json()
                    content = data['choices'][0]['message']['content']
                    finish = ""
                    try:
                        finish = data['choices'][0].get('finish_reason', '') or ""
                    except Exception:
                        finish = ""
                    # A3: 标注被截断的输出，供上层重试/降粒度处理
                    if finish == "length":
                        return content + "\n<<TRUNCATED>>"
                    return content
                # 非 2xx：可重试状态码才重试，否则立即抛配置诊断
                if resp.status_code not in _RETRYABLE_STATUS:
                    raise _diagnose_http_error(resp, self.base_url)
                last_exc = _diagnose_http_error(resp, self.base_url)

            # 还有重试机会则退避后继续
            if attempt < _MAX_RETRIES:
                time.sleep(min(8.0, 1.5 * (2 ** attempt)))

        # 重试耗尽
        if isinstance(last_exc, Exception):
            raise last_exc
        raise LLMConfigError(message="LLM 请求失败", diagnosis="未知错误",
                             suggestion="请稍后重试。")

    def validate(self) -> dict:
        """快速验证：发送一个最小请求，检测配置是否有效。"""
        if not self.base_url:
            provider_guide = _detect_provider("") or ""
            return {"ok": False, "message": "未配置 Base URL",
                    "suggestion": "请填写 LLM 接口地址，如 https://api.openai.com/v1"}
        if not self.api_key:
            hint = _detect_provider(self.base_url)
            return {"ok": False, "message": "未配置 API Key",
                    "suggestion": ("请填写 API Key。" + (f"\n{hint}" if hint else ""))}
        if not self.model:
            hint = _detect_provider(self.base_url)
            return {"ok": False, "message": "未配置模型名称",
                    "suggestion": ("请填写模型名称，如 gpt-4o-mini。" + (f"\n{hint}" if hint else ""))}
        url = f"{self.base_url}/chat/completions"
        try:
            resp = requests.post(url, json={
                'model': self.model,
                'messages': [{"role": "user", "content": "hi"}],
                'max_tokens': 5,
            }, headers=self._build_headers(), timeout=15)
        except requests.exceptions.ConnectionError as e:
            hint = _detect_provider(self.base_url)
            detail = str(e)[:150]
            sug = f"请检查 Base URL 是否可访问，以及网络连接是否正常。"
            if hint:
                sug += f"\n{hint}"
            return {"ok": False, "message": f"无法连接到服务",
                    "suggestion": sug, "detail": detail}
        except requests.exceptions.Timeout:
            return {"ok": False, "message": "连接超时（15s）",
                    "suggestion": "服务响应过慢，请检查服务是否正常运行。"}
        if not resp.ok:
            err = _diagnose_http_error(resp, self.base_url)
            return {"ok": False, "message": err.message,
                    "suggestion": err.suggestion, "detail": err.diagnosis}
        return {"ok": True, "message": f"✅ 连接成功，模型 {self.model} 可用"}


class MockProvider(LLMProvider):
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2,
             max_tokens: int = 4096) -> str:
        return json.dumps({
            "summary": "⚠️ 当前使用的是【模拟模式】，并未对文档做任何分析。\n请到配置页面设置真实的 LLM（如 OpenAI / DeepSeek / Ollama）再重新审稿。",
            "revised_text": "【模拟模式未生成任何内容】",
            "issues": [],
            "references": []
        }, ensure_ascii=False)

    def validate(self) -> dict:
        return {"ok": True, "message": "模拟模式无需验证（不会分析真实文档）",
                "warning": "模拟模式下审稿结果无意义，请配置真实 LLM"}


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
        extra_headers = getattr(llm_cfg, 'extra_headers', '')
        if provider == 'openai_compatible':
            return OpenAICompatibleProvider(base_url=base_url, api_key=api_key,
                                            model=model, extra_headers=extra_headers)
        if provider == 'ollama_compatible':
            return OllamaCompatibleProvider(base_url=base_url, api_key=api_key,
                                            model=model, extra_headers=extra_headers)
        return MockProvider()

    @classmethod
    def from_hybrid_phase(cls, config: object, phase: int = 1) -> LLMProvider:
        """从两阶段配置中获取指定阶段的 provider。
        若该阶段未配置 model/api_key，自动回退到主 llm 配置。
        """
        hybrid = getattr(config, 'hybrid_llm', None)
        if hybrid is None:
            return cls.from_config(config)
        prefix = f"phase{phase}_"
        provider     = getattr(hybrid, f"{prefix}provider", 'openai_compatible')
        base_url     = getattr(hybrid, f"{prefix}base_url", '')
        api_key      = getattr(hybrid, f"{prefix}api_key", '')
        model        = getattr(hybrid, f"{prefix}model", '')
        extra_headers = getattr(hybrid, f"{prefix}extra_headers", '')
        # Fallback: if phase has no api_key, inherit from main llm config
        if not api_key:
            llm_cfg = getattr(config, 'llm', None)
            api_key = getattr(llm_cfg, 'api_key', '') if llm_cfg else ''
        if provider == 'ollama_compatible':
            return OllamaCompatibleProvider(base_url=base_url, api_key=api_key,
                                            model=model, extra_headers=extra_headers)
        if provider == 'openai_compatible':
            return OpenAICompatibleProvider(base_url=base_url, api_key=api_key,
                                            model=model, extra_headers=extra_headers)
        return MockProvider()
