from __future__ import annotations

import json
from typing import Any, Dict


def parse_review_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    # Strip markdown code fences (```json ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # Fall back: extract first JSON object found in the text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass
    return {
        "summary": text[:300],
        "revised_text": text,
        "issues": [],
        "references": [],
    }
