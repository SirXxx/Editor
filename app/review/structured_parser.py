from __future__ import annotations

import json
from typing import Any, Dict


def parse_review_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        return {
            'summary': text[:300],
            'revised_text': text,
            'issues': [],
            'references': []
        }
