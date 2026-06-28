from __future__ import annotations

import json
from typing import Any, Dict, Tuple


def parse_review_json(text: str) -> Dict[str, Any]:
    parsed, _ok = parse_review_json_ex(text)
    return parsed


def parse_review_json_ex(text: str) -> Tuple[Dict[str, Any], bool]:
    """解析模型返回的 JSON。

    返回 ``(dict, ok)``：``ok=False`` 表示无法解析为合法 JSON（可能被截断或
    模型输出了非 JSON 文本），上层据此判定“解析失败”，避免把失败静默当作
    “无问题”。
    """
    text = (text or "").strip()
    # 去除截断标记（来自 provider 对 finish_reason=length 的标注）
    truncated = "<<TRUNCATED>>" in text
    text = text.replace("<<TRUNCATED>>", "").strip()
    # Strip markdown code fences (```json ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    try:
        return json.loads(text), (not truncated)
    except Exception:
        pass
    # Fall back: extract first JSON object found in the text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1]), (not truncated)
        except Exception:
            pass
    # 彻底失败
    return ({
        "summary": text[:300],
        "revised_text": text,
        "issues": [],
        "references": [],
    }, False)
