from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _to_abs_path(value: str | Path) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def env_path(env_name: str, default: str | Path) -> Path:
    raw = os.getenv(env_name, "").strip()
    if raw:
        return _to_abs_path(raw)
    return _to_abs_path(default)


DATA_DIR = env_path("APP_DATA_DIR", "data")
WORKSPACE_DIR = env_path("APP_WORKSPACE_DIR", DATA_DIR / "workspace")
INPUT_DIR = env_path("APP_INPUT_DIR", DATA_DIR / "input")
OUTPUT_DIR = env_path("APP_OUTPUT_DIR", DATA_DIR / "output")
KB_DIR = env_path("APP_KB_DIR", DATA_DIR / "kb")

CONFIG_PATH = env_path("APP_CONFIG_PATH", WORKSPACE_DIR / "app_config.json")
API_KEYS_PATH = env_path("APP_API_KEYS_PATH", WORKSPACE_DIR / "api_keys.json")
REVIEW_RULES_PATH = env_path("APP_REVIEW_RULES_PATH", WORKSPACE_DIR / "review_rules.json")
SENSITIVE_LEXICON_PATH = env_path(
    "APP_SENSITIVE_LEXICON_PATH", WORKSPACE_DIR / "sensitive_lexicon.json"
)
MY_RULES_MD_PATH = env_path("APP_MY_RULES_PATH", DATA_DIR / "my_rules.md")


def ensure_runtime_dirs() -> None:
    for d in (DATA_DIR, WORKSPACE_DIR, INPUT_DIR, OUTPUT_DIR, KB_DIR):
        d.mkdir(parents=True, exist_ok=True)
