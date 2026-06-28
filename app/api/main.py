from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, Optional

from docx import Document
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import AppConfig
from app.exporters import export_review_result
from app.kb.manager import KnowledgeBaseManager
from app.llm.providers import LLMProviderFactory, LLMConfigError
from app.review.orchestrator import ReviewOrchestrator
from app.review.reviewer import Reviewer
from app.review.rules import get_rules_manager
from app.review.sensitive_scanner import (
    scan_pages,
    get_lexicon_dict,
    save_lexicon_dict,
    ensure_lexicon_file,
)
from app.models.schemas import ReviewIssue
from app.config import BUILTIN_PRESETS

BASE_DIR = Path(__file__).resolve().parents[2]
UI_DIR = BASE_DIR / "app" / "ui"
DATA_DIR = BASE_DIR / "data"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = DATA_DIR / "output"
WORKSPACE_DIR = DATA_DIR / "workspace"
for d in [INPUT_DIR, OUTPUT_DIR, WORKSPACE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI Editor Review System")
app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

config = AppConfig.load()
kb_manager = KnowledgeBaseManager(base_dir=str(DATA_DIR / "kb"), config=config)

TASKS: Dict[str, Dict[str, Any]] = {}
TASK_LOCK = Lock()


class TaskCancelledException(Exception):
    """任务被用户取消时抛出。"""


class LLMConfigPayload(BaseModel):
    provider: str = "mock"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    extra_headers: str = ""


class HybridLLMConfigPayload(BaseModel):
    phase1_provider: str = "openai_compatible"
    phase1_base_url: str = "https://api.llm-incubator.automotive.cloud/prod/v0/llm"
    phase1_api_key: str = ""
    phase1_model: str = "DeepSeek-V3.2"
    phase1_extra_headers: str = '{"X-Application-Name": "ai-editor-review"}'
    phase2_provider: str = "openai_compatible"
    phase2_base_url: str = "https://api.llm-incubator.automotive.cloud/prod/v0/llm"
    phase2_api_key: str = ""
    phase2_model: str = "DeepSeek-V4-Pro"
    phase2_extra_headers: str = '{"X-Application-Name": "ai-editor-review"}'


class EmbeddingConfigPayload(BaseModel):
    provider: str = "simple"
    base_url: str = ""
    api_key: str = ""
    model: str = "text-embedding-3-small"


class KBRebuildPayload(BaseModel):
    folder: str = "data/kb"
    category: str = "general"


class KBImportUrlPayload(BaseModel):
    url: str
    category: str = "general"


class KBBackendPayload(BaseModel):
    backend: str = "simple"
    category: Optional[str] = None


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(UI_DIR / "favicon.svg"), media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((UI_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api_keys")
def get_api_keys() -> Dict[str, Any]:
    """返回已保存的 API Key 列表（Key 值脱敏）。"""
    keys_path = DATA_DIR / "workspace" / "api_keys.json"
    if not keys_path.exists():
        return {"ok": True, "keys": {}}
    try:
        raw = json.loads(keys_path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": True, "keys": {}}
    safe = {
        k: {
            "base_url": v.get("base_url", ""),
            "note": v.get("note", ""),
            "key_preview": (v.get("key", "")[:8] + "…" if v.get("key") else ""),
        }
        for k, v in raw.items()
    }
    return {"ok": True, "keys": safe}


@app.post("/api_keys/switch/{key_id}")
def switch_api_key(key_id: str) -> Dict[str, Any]:
    """切换活跃 API Key（自动更新 Phase1/Phase2 的 key）。"""
    keys_path = DATA_DIR / "workspace" / "api_keys.json"
    if not keys_path.exists():
        return {"ok": False, "message": "未找到已保存的 Key"}
    raw = json.loads(keys_path.read_text(encoding="utf-8"))
    if key_id not in raw:
        return {"ok": False, "message": f"Key '{key_id}' 不存在"}
    entry = raw[key_id]
    new_key = entry.get("key", "")
    new_url = entry.get("base_url", "")
    # Apply to both phases if same domain, otherwise keep base_url per-phase
    config.llm.api_key = new_key
    config.llm.base_url = new_url
    config.hybrid_llm.phase1_api_key = new_key
    config.hybrid_llm.phase2_api_key = new_key
    if new_url:
        config.hybrid_llm.phase1_base_url = new_url
        config.hybrid_llm.phase2_base_url = new_url
    config.save()
    return {"ok": True, "message": f"已切换到：{entry.get('note', key_id)}",
            "key_id": key_id, "base_url": new_url}


@app.post("/api_keys/save")
def save_api_key(payload: Dict[str, Any] = None) -> Dict[str, Any]:
    """保存一个新的 API Key 记录。"""
    if not payload:
        return {"ok": False, "message": "payload 为空"}
    key_id  = payload.get("id", "").strip()
    key_val = payload.get("key", "").strip()
    base_url = payload.get("base_url", "").strip()
    note    = payload.get("note", "").strip()
    if not key_id or not key_val:
        return {"ok": False, "message": "id 和 key 不能为空"}
    keys_path = DATA_DIR / "workspace" / "api_keys.json"
    try:
        existing = json.loads(keys_path.read_text(encoding="utf-8")) if keys_path.exists() else {}
    except Exception:
        existing = {}
    existing[key_id] = {"key": key_val, "base_url": base_url, "note": note}
    keys_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "message": f"API Key '{key_id}' 已保存"}


@app.get("/config")
def get_config() -> Dict[str, Any]:
    return config.to_dict()


@app.post("/config")
def save_config(payload: LLMConfigPayload) -> Dict[str, Any]:
    config.llm.provider = payload.provider
    config.llm.base_url = payload.base_url
    config.llm.api_key = payload.api_key
    config.llm.model = payload.model
    config.llm.extra_headers = payload.extra_headers
    config.save()
    return {"ok": True, "message": "LLM 配置已保存", "config": config.to_dict()}


@app.post("/config/validate")
def validate_config(payload: LLMConfigPayload) -> Dict[str, Any]:
    """即时验证 LLM 配置，发送一个测试请求并返回诊断结果。"""
    from app.llm.providers import LLMConfigError, OpenAICompatibleProvider, MockProvider
    if payload.provider == "mock":
        return {"ok": True, "message": "模拟模式无需验证",
                "warning": "模拟模式不会分析真实文档内容，建议配置真实 LLM"}
    try:
        provider = OpenAICompatibleProvider(
            base_url=payload.base_url,
            api_key=payload.api_key,
            model=payload.model,
            extra_headers=payload.extra_headers,
        )
        result = provider.validate()
        return result
    except LLMConfigError as e:
        return {"ok": False, **e.to_dict()}
    except Exception as e:
        return {"ok": False, "message": str(e), "suggestion": "请检查配置参数。"}


@app.get("/config/hybrid")
def get_hybrid_config() -> Dict[str, Any]:
    from dataclasses import asdict
    return {"ok": True, "hybrid_llm": asdict(config.hybrid_llm)}


@app.post("/config/hybrid")
def save_hybrid_config(payload: HybridLLMConfigPayload) -> Dict[str, Any]:
    from dataclasses import asdict
    config.hybrid_llm.phase1_provider = payload.phase1_provider
    config.hybrid_llm.phase1_base_url = payload.phase1_base_url
    config.hybrid_llm.phase1_api_key = payload.phase1_api_key
    config.hybrid_llm.phase1_model = payload.phase1_model
    config.hybrid_llm.phase1_extra_headers = payload.phase1_extra_headers
    config.hybrid_llm.phase2_provider = payload.phase2_provider
    config.hybrid_llm.phase2_base_url = payload.phase2_base_url
    config.hybrid_llm.phase2_api_key = payload.phase2_api_key
    config.hybrid_llm.phase2_model = payload.phase2_model
    config.hybrid_llm.phase2_extra_headers = payload.phase2_extra_headers
    config.save()
    return {"ok": True, "message": "两阶段模型配置已保存", "hybrid_llm": asdict(config.hybrid_llm)}


@app.post("/config/hybrid/validate")
def validate_hybrid_config(payload: HybridLLMConfigPayload) -> Dict[str, Any]:
    """验证混合模型两个阶段的连接，返回各自的诊断结果。"""
    from app.llm.providers import LLMConfigError, OpenAICompatibleProvider
    results: Dict[str, Any] = {}
    for phase, p, url, key, model, headers in [
        ("phase1", payload.phase1_provider, payload.phase1_base_url,
         payload.phase1_api_key, payload.phase1_model, payload.phase1_extra_headers),
        ("phase2", payload.phase2_provider, payload.phase2_base_url,
         payload.phase2_api_key, payload.phase2_model, payload.phase2_extra_headers),
    ]:
        if p == "mock":
            results[phase] = {"ok": True, "message": "模拟模式"}
            continue
        try:
            prov = OpenAICompatibleProvider(base_url=url, api_key=key,
                                            model=model, extra_headers=headers)
            results[phase] = prov.validate()
        except LLMConfigError as e:
            results[phase] = {"ok": False, **e.to_dict()}
        except Exception as e:
            results[phase] = {"ok": False, "message": str(e)}
    all_ok = all(v.get("ok") for v in results.values())
    return {"ok": all_ok, "results": results}


@app.post("/embedding/config")
def save_embedding_config(payload: EmbeddingConfigPayload) -> Dict[str, Any]:
    config.embedding.provider = payload.provider
    config.embedding.base_url = payload.base_url
    config.embedding.api_key = payload.api_key
    config.embedding.model = payload.model
    config.save()
    return {"ok": True, "message": "Embedding 配置已保存", "config": config.to_dict()}


@app.get("/config/presets")
def get_presets() -> Dict[str, Any]:
    """返回所有内置预设配置（不含 API Key）。"""
    safe = []
    for p in BUILTIN_PRESETS:
        safe.append({k: v for k, v in p.items()})  # keys including all metadata
    return {"ok": True, "presets": safe}


@app.post("/config/apply_preset/{preset_id}")
def apply_preset(preset_id: str) -> Dict[str, Any]:
    """将指定预设应用为当前主模型配置（保留已有 API Key）。"""
    preset = next((p for p in BUILTIN_PRESETS if p["id"] == preset_id), None)
    if not preset:
        return {"ok": False, "message": f"预设 {preset_id} 不存在"}
    config.llm.provider = preset["provider"]
    config.llm.base_url = preset["base_url"]
    config.llm.model = preset["model"]
    config.llm.extra_headers = preset.get("extra_headers", "")
    # Keep existing API key unless preset provides one
    if preset.get("api_key"):
        config.llm.api_key = preset["api_key"]
    config.save()
    return {"ok": True, "message": f"已应用预设：{preset['name']}", "config": config.to_dict()}


@app.get("/output/list")
def list_outputs() -> Dict[str, Any]:
    """列出 output 文件夹中已保存的审稿结果文件。"""
    files = []
    for f in sorted(OUTPUT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix in ('.json', '.md', '.csv', '.docx', '.txt'):
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
                "type": f.suffix.lstrip('.'),
            })
    return {"ok": True, "files": files[:50]}


@app.get("/output/download/{filename}")
def download_output(filename: str):
    """下载 output 文件夹中的指定文件。"""
    # Security: only allow files directly in OUTPUT_DIR
    safe_path = OUTPUT_DIR / Path(filename).name
    if not safe_path.exists() or not safe_path.is_file():
        return {"ok": False, "message": "文件不存在"}
    media_types = {
        '.json': 'application/json',
        '.md': 'text/markdown',
        '.csv': 'text/csv',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.txt': 'text/plain',
    }
    mt = media_types.get(safe_path.suffix, 'application/octet-stream')
    return FileResponse(path=str(safe_path), filename=safe_path.name, media_type=mt)


@app.delete("/output/{filename}")
def delete_output_file(filename: str) -> Dict[str, Any]:
    """删除 output 文件夹中的单个文件。"""
    safe_path = OUTPUT_DIR / Path(filename).name
    if not safe_path.exists() or not safe_path.is_file():
        return {"ok": False, "message": "文件不存在"}
    safe_path.unlink()
    return {"ok": True, "message": f"已删除：{safe_path.name}"}


@app.post("/output/clear")
def clear_output() -> Dict[str, Any]:
    """清空 output 文件夹中所有审稿结果文件。"""
    deleted, errors = [], []
    for f in OUTPUT_DIR.iterdir():
        if f.is_file() and f.suffix in ('.json', '.md', '.csv', '.docx', '.txt'):
            try:
                f.unlink()
                deleted.append(f.name)
            except Exception as e:
                errors.append(f"{f.name}: {e}")
    return {
        "ok": True,
        "deleted": len(deleted),
        "errors": errors,
        "message": f"已清除 {len(deleted)} 个文件" + (f"，{len(errors)} 个失败" if errors else ""),
    }


@app.post("/tasks/clear")
def clear_tasks() -> Dict[str, Any]:
    """清除所有历史任务记录（不删除文件）。"""
    with TASK_LOCK:
        count = len(TASKS)
        TASKS.clear()
    return {"ok": True, "message": f"已清除 {count} 条任务记录"}


@app.get("/config/rules")
def get_rules() -> Dict[str, Any]:
    """获取所有自定义审稿规则。"""
    mgr = get_rules_manager()
    return {"ok": True, **mgr.to_dict()}


class RuleTogglePayload(BaseModel):
    rule_id: str
    enabled: bool


class RuleSavePayload(BaseModel):
    rule_states: Dict[str, bool] = {}
    custom_text: str = ""
    custom_rules: list = []


@app.post("/config/rules")
def save_rules(payload: RuleSavePayload) -> Dict[str, Any]:
    """批量保存规则状态 + 自定义文本。"""
    mgr = get_rules_manager()
    mgr.set_custom_text(payload.custom_text)
    for rule_id, enabled in payload.rule_states.items():
        mgr.set_rule_enabled(rule_id, enabled)
    for custom_rule in payload.custom_rules:
        if isinstance(custom_rule, dict) and custom_rule.get("id") and custom_rule.get("prompt_instruction"):
            mgr.add_custom_rule(custom_rule)
    mgr.save()
    return {"ok": True, "message": "规则已保存", **mgr.to_dict()}


@app.get("/config/rules/file")
def get_rules_file() -> Dict[str, Any]:
    """读取 data/my_rules.md 文件内容。"""
    rules_file = DATA_DIR / "my_rules.md"
    if not rules_file.exists():
        return {"ok": True, "content": "", "exists": False}
    content = rules_file.read_text(encoding="utf-8")
    return {"ok": True, "content": content, "exists": True}


@app.post("/config/rules/file")
def save_rules_file(payload: Dict[str, Any]) -> Dict[str, Any]:
    """保存 data/my_rules.md 文件内容，并同步到自定义规则文本。"""
    content = payload.get("content", "")
    rules_file = DATA_DIR / "my_rules.md"
    rules_file.write_text(content, encoding="utf-8")
    # Extract plain-text rules (non-comment lines) and sync to custom_text
    lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        # Skip markdown headers, empty lines, HTML comments
        if not stripped or stripped.startswith("#") or stripped.startswith("<!--") or stripped.startswith("-->") or stripped.startswith(">"):
            continue
        # Keep bullet points
        if stripped.startswith("-"):
            lines.append(stripped[1:].strip())
    mgr = get_rules_manager()
    if lines:
        mgr.set_custom_text("\n".join(lines))
        mgr.save()
    return {"ok": True, "message": f"规则文件已保存，同步了 {len(lines)} 条规则", "synced_count": len(lines)}


@app.get("/config/sensitive_lexicon")
def get_sensitive_lexicon() -> Dict[str, Any]:
    """读取当前生效的敏感词库（内置 + 用户自定义合并结果）。"""
    ensure_lexicon_file()
    return {"ok": True, "lexicon": get_lexicon_dict()}


@app.post("/config/sensitive_lexicon")
def save_sensitive_lexicon(payload: Dict[str, Any]) -> Dict[str, Any]:
    """保存编辑后的敏感词库。"""
    data = payload.get("lexicon", payload)
    try:
        save_lexicon_dict(data)
    except Exception as e:
        return {"ok": False, "message": f"保存失败：{e}"}
    cats = (data.get("categories") or {})
    total = sum(len(c.get("terms", [])) for c in cats.values())
    return {"ok": True, "message": f"敏感词库已保存，共 {len(cats)} 类 {total} 词"}



@app.post("/kb/backend")
def save_kb_backend(payload: KBBackendPayload) -> Dict[str, Any]:
    if payload.category:
        config.vector_backends[payload.category] = payload.backend
    else:
        for category in ["general", "editor", "law", "biology", "chemistry", "physics"]:
            config.vector_backends[category] = payload.backend
    config.save()
    return {"ok": True, "message": "向量后端已保存", "vector_backends": config.vector_backends}


@app.post("/preset/editor-mode")
def preset_editor_mode() -> Dict[str, Any]:
    config.review.default_category = "editor"
    if not config.llm.model:
        config.llm.provider = "mock"
        config.llm.model = "editor-reviewer"
    config.save()
    return {"ok": True, "message": "已切换到编辑审稿模式", "config": config.to_dict()}


@app.get("/kb/categories")
def kb_categories() -> Dict[str, Any]:
    return {"ok": True, "categories": kb_manager.list_categories()}


@app.post("/kb/rebuild")
def kb_rebuild(payload: KBRebuildPayload) -> Dict[str, Any]:
    folder = payload.folder
    if not Path(folder).is_absolute():
        folder = str(BASE_DIR / folder)
    count = kb_manager.rebuild_from_folder(folder=folder, category=payload.category)
    return {"ok": True, "message": f"知识库已重建：{payload.category}", "count": count}


@app.post("/kb/import_url")
def kb_import_url(payload: KBImportUrlPayload) -> Dict[str, Any]:
    doc = kb_manager.import_url(payload.url, category=payload.category)
    return {"ok": True, "message": "网页知识已导入", "document": doc}


def _sanitize_filename(name: str) -> str:
    """清洗上传文件名，防止路径穿越与非法字符（O2）。"""
    import os as _os
    base = _os.path.basename(str(name or "")).replace("\\", "_").replace("/", "_")
    # 去除驱动器/穿越片段与控制字符
    base = re.sub(r'[\x00-\x1f<>:"|?*]', "_", base).strip().strip(".")
    base = base.lstrip(".")  # 防止隐藏文件 / 仅点
    if not base:
        base = "document"
    return base[:180]


_PUNCT_RE = re.compile(r"[\s\u3000，。、；：！？“”‘’（）()\[\]【】<>《》·\-—~,.;:!?\"'`]+")


def _normalize_for_match(s: str) -> str:
    """归一化文本用于回填/幻觉匹配：去空白与中英标点，统一全角半角数字字母。"""
    if not s:
        return ""
    out = []
    for ch in s:
        cp = ord(ch)
        # 全角数字/字母 → 半角
        if 0xFF10 <= cp <= 0xFF19 or 0xFF21 <= cp <= 0xFF3A or 0xFF41 <= cp <= 0xFF5A:
            out.append(chr(cp - 0xFEE0))
        else:
            out.append(ch)
    return _PUNCT_RE.sub("", "".join(out))


@app.post("/tasks/review_document")
async def create_review_task(
    file: UploadFile = File(...),
    kb_category: str = Form(default=""),
    scan_mode: bool = Form(default=False),
    include_front_pages: bool = Form(default=False),
    export_markdown: bool = Form(default=False),
    export_csv: bool = Form(default=False),
    dimensions: str = Form(default=""),      # comma-separated, empty = all
    page_start: int = Form(default=1),       # first page to review (1-based)
    page_end: int = Form(default=0),         # last page, 0 = all pages
) -> Dict[str, Any]:
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        return {"ok": False, "message": "仅支持 PDF 和 DOCX 文件"}

    # Parse selected dimensions
    from app.review.dimensions import REVIEW_DIMENSIONS
    all_dim_keys = list(REVIEW_DIMENSIONS.keys())
    if dimensions.strip():
        selected_dims = [d.strip() for d in dimensions.split(",") if d.strip() in all_dim_keys]
        if not selected_dims:
            selected_dims = all_dim_keys
    else:
        selected_dims = all_dim_keys

    # Normalize page range (0 means "all")
    p_start = max(1, page_start)
    p_end   = max(0, page_end)   # 0 = no limit

    task_id = str(uuid.uuid4())
    # O2: 防路径穿越——只取文件名部分并清洗非法字符
    safe_name = _sanitize_filename(file.filename)
    input_path = INPUT_DIR / f"{task_id}_{safe_name}"
    with input_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    task = {
        "task_id": task_id,
        "status": "queued",
        "progress": 0,
        "message": "任务已进入队列",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "file_name": file.filename,
        "file_type": suffix,
        "kb_category": kb_category or "",
        "scan_mode": scan_mode,
        "include_front_pages": include_front_pages,
        "dimensions": selected_dims,
        "page_start": p_start,
        "page_end": p_end,
        "result": None,
        "error": None,
        "input_path": str(input_path),
        "source_text": "",
        "events": [],
    }
    with TASK_LOCK:
        TASKS[task_id] = task

    thread = Thread(
        target=_run_review_task_sync,
        args=(task_id, str(input_path), file.filename, suffix, kb_category,
              scan_mode, include_front_pages, export_markdown, export_csv, selected_dims, p_start, p_end),
        daemon=True,
    )
    thread.start()
    page_desc = f"第{p_start}-{p_end}页" if p_end else f"第{p_start}页起全文"
    return {"ok": True, "task_id": task_id, "status": "queued",
            "dimensions": selected_dims, "page_range": page_desc}


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> Dict[str, Any]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return {"ok": False, "message": "任务不存在"}
        return _public_task(task)


@app.get("/tasks")
def list_tasks() -> Dict[str, Any]:
    with TASK_LOCK:
        items = sorted(TASKS.values(), key=lambda x: x.get("created_at", ""), reverse=True)
        return {"ok": True, "tasks": [_public_task(t) for t in items[:50]]}


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> Dict[str, Any]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return {"ok": False, "message": "任务不存在"}
        if task["status"] in ("completed", "failed", "cancelled"):
            return {"ok": False, "message": "任务已结束"}
        task["cancelled"] = True
        task["status"] = "cancelled"
        task["message"] = "任务已取消"
        task["updated_at"] = datetime.now().isoformat()
    return {"ok": True, "message": "任务已取消"}


@app.get("/tasks/{task_id}/export_docx")
def export_task_docx(task_id: str):
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return {"ok": False, "message": "任务不存在"}
        if task.get("status") != "completed" or not task.get("result"):
            return {"ok": False, "message": "任务未完成，无法导出"}
        result = task["result"]
        file_name = task.get("file_name", "document")
        kb_category = task.get("kb_category", "") or "全部"

    stem = Path(file_name).stem
    out_path = OUTPUT_DIR / f"{stem}_review_report.docx"
    _write_review_docx(out_path, file_name, kb_category, result)
    return FileResponse(path=str(out_path), filename=out_path.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def _public_task(task: Dict[str, Any]) -> Dict[str, Any]:
    pub: Dict[str, Any] = {
        "ok": True,
        "task_id": task["task_id"],
        "status": task["status"],
        "progress": task["progress"],
        "message": task["message"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "file_name": task.get("file_name", ""),
        "file_type": task.get("file_type", ""),
        "kb_category": task.get("kb_category", ""),
        "scan_mode": task.get("scan_mode", False),
        "result": task.get("result"),
        "error": task.get("error"),
        "partial_issues": task.get("partial_issues", []),
        "chunk_index": task.get("chunk_index", 0),
        "total_chunks": task.get("total_chunks", 1),
    }
    # Include source text for completed tasks (for frontend annotation rendering)
    if task.get("status") == "completed":
        pub["source_text"] = task.get("source_text", "")
    return pub


def _emit_task_event(task_id: str, event: Dict[str, Any]) -> None:
    """Append an SSE event to the task's event log."""
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task is not None:
            task.setdefault("events", []).append(event)


@app.get("/stream/{task_id}")
async def stream_task(task_id: str, request: Request):
    """SSE endpoint: streams review progress events to the client in real time."""
    async def generate():
        cursor = 0
        while True:
            if await request.is_disconnected():
                break
            with TASK_LOCK:
                task = TASKS.get(task_id)
                if not task:
                    yield 'event: error\ndata: {"message":"task not found"}\n\n'
                    return
                events: list = list(task.get("events", []))
                status: str = task.get("status", "")

            new_events = events[cursor:]
            cursor += len(new_events)

            for evt in new_events:
                evt_type = evt.get("type", "message")
                yield f"event: {evt_type}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

            if status in ("completed", "failed", "cancelled") and cursor >= len(events):
                yield "event: stream_end\ndata: {}\n\n"
                break

            if not new_events:
                yield ": keepalive\n\n"

            await asyncio.sleep(0.2)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _update_task(task_id: str, **kwargs: Any) -> None:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.update(kwargs)
        task["updated_at"] = datetime.now().isoformat()


def _is_cancelled(task_id: str) -> bool:
    with TASK_LOCK:
        return bool(TASKS.get(task_id, {}).get("cancelled"))


def _run_review_task_sync(
    task_id: str,
    input_path: str,
    file_name: str,
    file_type: str,
    kb_category: str,
    scan_mode: bool,
    include_front_pages: bool,
    export_markdown: bool,
    export_csv: bool,
    dimensions: Optional[list] = None,
    page_start: int = 1,
    page_end: int = 0,
) -> None:
    try:
        _update_task(task_id, status="running", progress=5, message="正在提取文档内容")
        _emit_task_event(task_id, {"type": "started", "message": "开始处理文档"})

        # ── 1. 文本提取（支持页码范围）─────────────────────────────────────────
        page_line_index: Dict[str, Any] = {}
        total_pages = 0

        if file_type == ".pdf":
            import fitz as _fitz
            from app.extractors.text_cleaner import (
                preprocess_pages, strip_non_content_front_pages, page_text_layout_aware,
            )
            _doc = _fitz.open(input_path)
            total_pages = len(_doc)
            p0 = max(0, page_start - 1)
            p1 = (page_end if page_end > 0 else total_pages)
            p1 = min(p1, total_pages)
            page_range_desc = (f"第{p0+1}-{p1}页" if p1 < total_pages or p0 > 0
                               else f"全文共{total_pages}页")
            _update_task(task_id, progress=10,
                         message=f"正在解析 PDF 文本（{page_range_desc}，共{total_pages}页）")

            # Extract raw page texts（版面感知：用 block 坐标重排，缓解双栏乱序 P3）
            raw_pages: list = []
            low_text_pages: list = []
            for pi in range(p0, p1):
                page = _doc[pi]
                ptxt = page_text_layout_aware(page)
                raw_pages.append((pi + 1, ptxt))
                if len(ptxt.strip()) < 10:
                    low_text_pages.append(pi)

            # P1: 扫描件/图片型 PDF — 显式 scan_mode 或检测到大量空文本页时走 OCR
            need_ocr = scan_mode or (len(low_text_pages) >= max(1, (p1 - p0) * 0.6))
            if need_ocr:
                try:
                    from app.extractors.layout_ocr import LayoutOCRProcessor
                    ocr = LayoutOCRProcessor()
                    if ocr.ocr.available():
                        _update_task(task_id, progress=10,
                                     message="检测到扫描件/图片型 PDF，正在 OCR 识别（较慢）…")
                        _emit_task_event(task_id, {"type": "preprocess_notice",
                                                   "message": "已启用 OCR 识别扫描件文本"})
                        out_dir = str(WORKSPACE_DIR / f"{task_id}_ocr")
                        ocr_pages = ocr.extract_pdf_pages(str(input_path), out_dir)
                        # 仅替换所选页范围
                        ocr_map = {item["page"]: item.get("text", "") for item in ocr_pages}
                        raw_pages = [(pi + 1, ocr_map.get(pi + 1, dict(raw_pages).get(pi + 1, "")))
                                     for pi in range(p0, p1)]
                    elif scan_mode:
                        _emit_task_event(task_id, {"type": "chunk_error", "dimension": "ocr",
                            "error": "OCR 引擎不可用（未安装 paddleocr），扫描件可能无法识别"})
                except Exception as ocr_err:
                    _emit_task_event(task_id, {"type": "chunk_error", "dimension": "ocr",
                                               "error": f"OCR 失败：{ocr_err}"})
            _doc.close()

            # Drop non-content leading pages (e.g. vendor review cover/overview pages)
            skipped_pages = []
            if include_front_pages:
                _emit_task_event(task_id, {
                    "type": "preprocess_notice",
                    "skipped_pages": [],
                    "message": "当前为全页审稿模式：已包含前置页（可能含审校封面/概览）",
                })
            if not include_front_pages:
                filtered_pages, skipped_pages = strip_non_content_front_pages(raw_pages)
                if skipped_pages:
                    raw_pages = filtered_pages
                    skipped_label = "、".join(str(p) for p in skipped_pages[:8])
                    if len(skipped_pages) > 8:
                        skipped_label += "…"
                    _emit_task_event(task_id, {
                        "type": "preprocess_notice",
                        "skipped_pages": skipped_pages,
                        "message": f"已自动跳过疑似非正文前置页：第{skipped_label}页",
                    })

            # Run full preprocessing pipeline: PUA→clean, headers removed, broken lines joined
            _update_task(task_id, progress=11, message="正在清洗 PDF 文本噪音…")
            clean_stats: Dict[str, Any] = {}
            cleaned_pages = preprocess_pages(raw_pages, stats=clean_stats)

            # P2: PUA 丢字告警——丢弃过多疑似字体编码异常，提示人工核对
            if clean_stats.get("pua_dropped", 0) >= 30:
                _emit_task_event(task_id, {
                    "type": "preprocess_notice",
                    "message": f"⚠️ 检测到 {clean_stats['pua_dropped']} 个无法识别的私用区字符"
                               f"（已用 □ 占位），疑似字体编码异常，请人工核对原文。",
                })

            # Build page_line_index from CLEANED text (for accurate issue matching)
            # 同时构建 page_offsets：每页在合并全文中的起始偏移（A1 精确页码映射）
            page_texts: list = []
            text_parts = []
            page_offsets: list = []   # [(start_offset, page_num), ...]
            running = 0
            for page_num, page_text in cleaned_pages:
                page_offsets.append((running, page_num))
                running += len(page_text) + 1   # +1 for the joining '\n'
                text_parts.append(page_text)
                page_texts.append((page_num, page_text))
                for li, line in enumerate(page_text.split('\n'), 1):
                    if line.strip():
                        key = line.strip()[:50]
                        if key not in page_line_index:
                            page_line_index[key] = {"page": page_num, "line": li}
            text = "\n".join(text_parts)

            _emit_task_event(task_id, {
                "type": "doc_parsed",
                "source_text": text[:60000],
                "text_length": len(text),
                "total_chunks": max(1, len(text) // 2000 + 1),
                "page_start": p0 + 1,
                "page_end": p1,
                "total_pages": total_pages,
                "message": f"PDF 解析完成（已清洗噪音）：{page_range_desc}，共 {len(text):,} 字",
            })
        else:
            _update_task(task_id, progress=10, message="正在提取 Word 文本")
            text = _extract_docx_text(input_path)
            page_offsets = []
            for i, line in enumerate(text.split('\n'), 1):
                if line.strip():
                    key = line.strip()[:50]
                    if key not in page_line_index:
                        page_line_index[key] = {"page": 1, "line": i}
            _emit_task_event(task_id, {
                "type": "doc_parsed",
                "source_text": text[:60000],
                "text_length": len(text),
                "total_chunks": max(1, len(text) // 2000 + 1),
                "message": f"Word 提取完成，共 {len(text):,} 字",
            })

        if not text.strip():
            raise ValueError("无法从文档中提取文本，请检查文件内容")

        source_preview = text[:60000]
        source_truncated = len(text) > 60000
        _update_task(task_id, source_text=source_preview, progress=12)
        if source_truncated:
            _emit_task_event(task_id, {
                "type": "preprocess_notice",
                "message": f"⚠️ 原文较长（{len(text):,} 字），原文预览仅显示前 6 万字；"
                           f"超出部分的问题仍会列出页码/行号，但不在预览区高亮。",
            })

        if _is_cancelled(task_id):
            raise TaskCancelledException()

        # ── 2. 构建 offset→page 映射（A1：基于真实字符偏移，取代等宽假设）──────
        chunk_size = 2000
        if file_type == ".pdf" and page_offsets:
            _po = list(page_offsets)   # [(start_offset, page_num), ...] 已按顺序

            def offset_to_page(off: int, _po=_po):
                page = _po[0][1]
                for start_off, pg in _po:
                    if off >= start_off:
                        page = pg
                    else:
                        break
                return page
        else:
            page_texts = []
            offset_to_page = None

        # ── 3. 两阶段并行审稿（Phase1/Phase2 始终使用各自独立配置）──────────────
        llm        = LLMProviderFactory.from_hybrid_phase(config, phase=1)
        llm_phase2 = LLMProviderFactory.from_hybrid_phase(config, phase=2)
        _emit_task_event(task_id, {
            "type": "hybrid_mode",
            "phase1_model": getattr(llm, "model", ""),
            "phase2_model": getattr(llm_phase2, "model", ""),
            "message": f"Phase1={getattr(llm,'model','')}  |  Phase2={getattr(llm_phase2,'model','')}",
        })

        orchestrator = ReviewOrchestrator(
            llm=llm,
            llm_phase2=llm_phase2,
            kb_manager=kb_manager,
            config=config,
            max_workers=3,
            chunk_size=chunk_size,
        )

        def event_callback(event_type: str, **kwargs) -> None:
            if _is_cancelled(task_id):
                raise TaskCancelledException()
            _emit_task_event(task_id, {"type": event_type, **kwargs})
            if event_type == "phase_start":
                phase = kwargs.get("phase", 1)
                _update_task(task_id,
                             progress=15 if phase == 1 else 55,
                             message=kwargs.get("message", f"第 {phase} 阶段"))
            elif event_type == "phase_done":
                phase = kwargs.get("phase", 1)
                _update_task(task_id,
                             progress=50 if phase == 1 else 90,
                             partial_issues=[])

        result = orchestrator.review_with_events(
            text=text,
            dimensions=dimensions or None,
            kb_category=kb_category or None,
            event_callback=event_callback,
            cancel_check=lambda: _is_cancelled(task_id),
            offset_to_page=offset_to_page,
        )

        if _is_cancelled(task_id):
            raise TaskCancelledException()

        result.source_file = input_path
        result.source_text = source_preview

        # ── 3.5 确定性敏感词/合规扫描（不依赖 LLM，保证召回）──────────────────
        # 政治/敏感把关不能仅靠在线模型，这里用可审计、可维护的词库做确定性扫描。
        scan_dims = [d for d in (dimensions or []) if d in ("politics", "sensitive", "copyright")]
        if scan_dims:
            scan_input = page_texts if (file_type == ".pdf" and page_texts) else [(1, text)]
            try:
                scanned = scan_pages(scan_input, dimensions=scan_dims)
            except Exception as scan_err:
                scanned = []
                _emit_task_event(task_id, {"type": "chunk_error",
                                           "dimension": "sensitive",
                                           "error": f"敏感词扫描失败：{scan_err}"})
            new_issues = []
            for d in scanned:
                try:
                    new_issues.append(ReviewIssue(**{
                        "issue_type": d["issue_type"],
                        "severity": d.get("severity", "medium"),
                        "page": d.get("page"),
                        "line": d.get("line"),
                        "original": d.get("original", ""),
                        "suggestion": d.get("suggestion", ""),
                        "reason": d.get("reason", ""),
                        "dimension": d.get("dimension"),
                        "confidence": d.get("confidence", 0.6),
                        "needs_review": d.get("needs_review", True),
                    }))
                except Exception:
                    continue
            if new_issues:
                result.issues.extend(new_issues)
                for d in scan_dims:
                    result.dimension_stats[d] = result.dimension_stats.get(d, 0) + \
                        sum(1 for i in new_issues if (i.dimension or i.issue_type) == d)
                _emit_task_event(task_id, {
                    "type": "chunk_done",
                    "chunk": -1,
                    "dimension": "sensitive",
                    "dim_name": "敏感词库扫描",
                    "issues": [i.model_dump() for i in new_issues],
                    "count": len(new_issues),
                    "message": f"确定性敏感词扫描命中 {len(new_issues)} 处（已标注需人工复核）",
                })

        # ── 4. 匹配精确页码 + 行号（通过 page_line_index 补全位置信息）──────────
        # 注意：orchestrator 只能给出“约第 X 页”的估算页码且不含行号，
        # 这里通过原文内容回查 page_line_index，取得精确的页码与行号。
        # A4：先对 key 做归一化（去空白/标点），提升 LLM 改写片段的回填命中率。
        norm_index = {_normalize_for_match(k): v for k, v in page_line_index.items()}
        norm_text = _normalize_for_match(text)
        for issue in result.issues:
            if not page_line_index or not issue.original:
                continue
            if issue.page and issue.line:
                continue  # already fully located
            orig = issue.original.strip()
            if len(orig) < 4:
                continue
            matched = False
            # Strategy 1: exact prefix-key match (strict→loose)
            for length in (40, 25, 15, 10):
                if len(orig) < length:
                    continue
                key = orig[:length]
                if key in page_line_index:
                    issue.page = page_line_index[key]["page"]
                    issue.line = page_line_index[key].get("line")
                    matched = True
                    break
            if matched:
                continue
            # Strategy 2 (A4): 归一化前缀匹配
            norm_orig = _normalize_for_match(orig)
            for length in (30, 20, 12, 8):
                if len(norm_orig) < length:
                    continue
                nk = norm_orig[:length]
                hit = norm_index.get(nk)
                if hit:
                    issue.page = hit["page"]
                    issue.line = hit.get("line")
                    matched = True
                    break
            if matched:
                continue
            # Strategy 3: substring scan — 用归一化开头互相包含来匹配
            head = norm_orig[:10]
            if head:
                for nkey, pos_info in norm_index.items():
                    if head in nkey or nkey[:10] in norm_orig:
                        issue.page = pos_info["page"]
                        issue.line = pos_info.get("line")
                        break

        # ── 4.5 幻觉校验（A5）：original 不在原文则降置信并标注需人工复核 ────────
        for issue in result.issues:
            if not issue.original or getattr(issue, "_scanner", False):
                continue
            no = _normalize_for_match(issue.original)
            if len(no) >= 6 and no not in norm_text:
                issue.confidence = min(issue.confidence, 0.3)
                issue.needs_review = True
                if "未在原文精确匹配" not in (issue.reason or ""):
                    issue.reason = (issue.reason or "") + "（原文未精确匹配，疑似改写/幻觉，需人工核对）"

        # ── 4.8 去重（A8）：同一 (page,line,归一化original) 合并，保留高严重度 ────
        _SEV_RANK = {"high": 3, "medium": 2, "low": 1}
        _dedup: Dict[tuple, "ReviewIssue"] = {}
        for issue in result.issues:
            k = (issue.dimension or issue.issue_type,
                 issue.page, issue.line,
                 _normalize_for_match(issue.original)[:40])
            prev = _dedup.get(k)
            if prev is None:
                _dedup[k] = issue
            else:
                if _SEV_RANK.get(issue.severity, 0) > _SEV_RANK.get(prev.severity, 0):
                    _dedup[k] = issue
        result.issues = list(_dedup.values())
        result.dimension_stats = {}
        for issue in result.issues:
            d = issue.dimension or issue.issue_type
            result.dimension_stats[d] = result.dimension_stats.get(d, 0) + 1

        # ── 4. 序列化问题列表（含页码行号）────────────────────────────────────
        issues_dicts = [
            i.model_dump() if hasattr(i, "model_dump") else dict(i)
            for i in result.issues
        ]
        # Enrich with display_location field for UI (page + line)
        for iss in issues_dicts:
            pg = iss.get("page") or ""
            ln = iss.get("line") or ""
            if pg and ln:
                iss["display_location"] = f"第 {pg} 页 第 {ln} 行"
            elif pg:
                iss["display_location"] = f"第 {pg} 页"
            else:
                iss["display_location"] = ""

        # ── 5. 导出文件（始终保存 JSON + TXT 到 output，可选 md/csv）────────────
        _update_task(task_id, progress=93, message="正在导出结果")
        exports: Dict[str, str] = {}
        stem = _sanitize_filename(Path(file_name).stem) or "document"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 按维度分组 issues
        issues_by_dim: Dict[str, list] = {}
        for iss in issues_dicts:
            dim = iss.get("dimension") or iss.get("issue_type", "other")
            issues_by_dim.setdefault(dim, []).append(iss)

        # Always: JSON（含按维度分组结构）
        save_path = OUTPUT_DIR / f"{stem}_{ts}_review.json"
        save_path.write_text(json.dumps({
            "summary": result.summary,
            "file_name": file_name,
            "created_at": datetime.now().isoformat(),
            "total_issues": len(issues_dicts),
            "dimension_stats": result.dimension_stats,
            "issues_by_dimension": issues_by_dim,   # 按维度分组
            "issues": issues_dicts,                  # 全量平铺（保持兼容）
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        exports["json"] = save_path.name

        # Always: readable TXT report（按维度分节）
        txt_path = OUTPUT_DIR / f"{stem}_{ts}_report.txt"
        _write_txt_report(txt_path, file_name, result.summary, issues_dicts, issues_by_dim)
        exports["txt"] = txt_path.name

        # Optional: markdown / csv
        if export_markdown:
            md_path = OUTPUT_DIR / f"{stem}_{ts}_review.md"
            export_review_result(result=result, file_path=str(md_path), mode="markdown")
            exports["markdown"] = md_path.name
        if export_csv:
            csv_path = OUTPUT_DIR / f"{stem}_{ts}_issues.csv"
            export_review_result(result=result, file_path=str(csv_path), mode="csv")
            exports["csv"] = csv_path.name

        output: Dict[str, Any] = {
            "summary": result.summary,
            "issues": issues_dicts,
            "revised_text": result.revised_text,
            "dimension_stats": result.dimension_stats,
            "skipped_pages": skipped_pages if file_type == ".pdf" else [],
            "source_text": source_preview,
            "source_truncated": source_truncated,
            "text_length": len(text),
            "failed_tasks": getattr(result, "failed_tasks", 0),
            "incomplete": getattr(result, "incomplete", False),
            "exports": exports,
            "file_type": file_type,
            "json_path": str(save_path),
        }

        _update_task(task_id, status="completed", progress=100, message="审稿完成",
                     result=output, source_text=source_preview)
        _emit_task_event(task_id, {"type": "task_completed", "exports": exports})

    except TaskCancelledException:
        _update_task(task_id, status="cancelled", progress=0, message="任务已取消")
    except LLMConfigError as e:
        # Surface config errors with actionable guidance
        friendly = f"⚙️ 配置错误：{e.message}"
        _update_task(task_id, status="failed", progress=0, message=friendly,
                     error=friendly,
                     error_detail={"type": "config_error", **e.to_dict()})
        _emit_task_event(task_id, {"type": "config_error", **e.to_dict()})
    except Exception as e:
        _update_task(task_id, status="failed", progress=100, message=f"任务失败：{e}", error=str(e))
        _emit_task_event(task_id, {"type": "task_failed", "error": str(e)})


def _extract_docx_text(file_path: str) -> str:
    doc = Document(file_path)
    parts = []
    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _write_txt_report(
    out_path: Path,
    file_name: str,
    summary: str,
    issues: list,
    issues_by_dim: Optional[Dict[str, list]] = None,
) -> None:
    """写入按维度分节的纯文本审稿报告。"""
    SEV_MAP  = {"high": "严重", "medium": "中等", "low": "轻微"}
    DIM_MAP  = {
        "grammar":     "📝 语法文字",
        "spelling":    "🔢 格式规范",
        "style":       "🎨 语气风格",
        "terminology": "🔬 专业术语",
        "logic":       "🧠 逻辑结构",
        "fact":        "🔍 事实核查",
        "qa_check":    "📋 题目校验",
        "politics":    "🛡️ 政治审查",
        "sensitive":   "🚫 敏感违禁词",
        "structure":   "🏗️ 框架结构",
        "copyright":   "©️ 版权与引用",
    }
    DIM_ORDER = ["politics", "sensitive", "copyright", "structure", "grammar", "spelling",
                 "style", "terminology", "logic", "fact", "qa_check"]

    # Build grouped dict if not provided
    if issues_by_dim is None:
        issues_by_dim = {}
        for iss in issues:
            dim = iss.get("dimension") or iss.get("issue_type", "other")
            issues_by_dim.setdefault(dim, []).append(iss)

    sev_counts = {"high": 0, "medium": 0, "low": 0}
    for iss in issues:
        sev_counts[iss.get("severity", "medium")] = sev_counts.get(iss.get("severity", "medium"), 0) + 1

    lines = [
        "=" * 65,
        "  AI 专业编辑审稿报告",
        f"  文件：{file_name}",
        f"  时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  总计：{len(issues)} 处问题  "
        f"（严重 {sev_counts['high']} · 中等 {sev_counts['medium']} · 轻微 {sev_counts['low']}）",
        "=" * 65,
        "",
        "▌ 审稿摘要",
        "-" * 65,
        summary or "（无）",
        "",
        "▌ 维度汇总",
        "-" * 65,
    ]

    # Dimension summary table
    for dim_key in DIM_ORDER:
        dim_issues = issues_by_dim.get(dim_key, [])
        if not dim_issues:
            continue
        label = DIM_MAP.get(dim_key, dim_key)
        h = sum(1 for i in dim_issues if i.get("severity") == "high")
        m = sum(1 for i in dim_issues if i.get("severity") == "medium")
        l = sum(1 for i in dim_issues if i.get("severity") == "low")
        lines.append(f"  {label:16}  共 {len(dim_issues):3d} 处  "
                     f"严重{h} · 中等{m} · 轻微{l}")

    lines += [""]

    # ── 问题明细：按「页 → 行」顺序排列（不再按维度分组）──────────────────────
    # 排序键：页码升序 → 行号升序 → 维度顺序；缺页码/行号的排到最后。
    _dim_rank = {k: i for i, k in enumerate(DIM_ORDER)}

    def _sort_key(iss: dict):
        pg = iss.get("page")
        ln = iss.get("line")
        pg_v = pg if isinstance(pg, int) else 10 ** 9          # 无页码排最后
        ln_v = ln if isinstance(ln, int) else 10 ** 9          # 无行号排该页最后
        dim = iss.get("dimension") or iss.get("issue_type", "")
        return (pg_v, ln_v, _dim_rank.get(dim, 999))

    ordered_issues = sorted(issues, key=_sort_key)

    lines += [
        "",
        f"{'=' * 65}",
        f"  问题明细（按页码、行号顺序）  共 {len(ordered_issues)} 处",
        f"{'=' * 65}",
    ]

    last_page = None
    for idx, iss in enumerate(ordered_issues, 1):
        pg  = iss.get("page") or ""
        ln  = iss.get("line") or ""
        # 翻到新页时插入页分隔标题，便于按页浏览
        if pg != last_page:
            last_page = pg
            page_title = f"第 {pg} 页" if pg else "（未定位页码）"
            lines += ["", f"────────────  {page_title}  ────────────"]
        if pg and ln:
            loc = f"第 {pg} 页 第 {ln} 行"
        elif pg:
            loc = f"第 {pg} 页"
        else:
            loc = "位置未定位"
        dim = iss.get("dimension") or iss.get("issue_type", "")
        dim_label = DIM_MAP.get(dim, dim)
        sev = SEV_MAP.get(iss.get("severity", ""), iss.get("severity", ""))
        nr  = "  ⚠需人工复核" if iss.get("needs_review") else ""
        lines.append(f"\n[{idx:3d}]  {loc}  [{dim_label}·{sev}]{nr}")
        lines.append(f"  原文：{iss.get('original', '')}")
        lines.append(f"  建议：{iss.get('suggestion', '')}")
        lines.append(f"  原因：{iss.get('reason', '')}")

    lines += ["", "=" * 65, "（报告结束）"]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _write_review_docx(out_path: Path, file_name: str, kb_category: str, result: Dict[str, Any]) -> None:
    DIM_LABELS = {
        "grammar":     "📝 语法文字",
        "spelling":    "🔢 格式规范",
        "style":       "🎨 语气风格",
        "terminology": "🔬 专业术语",
        "logic":       "🧠 逻辑结构",
        "fact":        "🔍 事实核查",
        "qa_check":    "📋 题目校验",
    }
    DIM_ORDER = ["grammar", "spelling", "style", "terminology", "logic", "fact", "qa_check"]
    SEV_LABELS = {"high": "严重", "medium": "中等", "low": "轻微"}

    doc = Document()
    doc.add_heading("AI 专业编辑审稿报告", level=0)
    doc.add_paragraph(f"原文件：{file_name}")
    doc.add_paragraph(f"知识库类别：{kb_category or '全部'}")
    doc.add_paragraph(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    doc.add_heading("一、审稿摘要", level=1)
    doc.add_paragraph(result.get("summary", ""))

    # Dimension stats
    dim_stats = result.get("dimension_stats", {})
    if dim_stats:
        doc.add_heading("二、维度汇总", level=1)
        stat_table = doc.add_table(rows=1, cols=3)
        stat_table.rows[0].cells[0].text = "维度"
        stat_table.rows[0].cells[1].text = "问题数"
        stat_table.rows[0].cells[2].text = "说明"
        for dk in DIM_ORDER:
            if dk in dim_stats:
                row = stat_table.add_row().cells
                row[0].text = DIM_LABELS.get(dk, dk)
                row[1].text = str(dim_stats[dk])
                row[2].text = ""

    # Issues grouped by dimension
    issues = result.get("issues", []) or []
    issues_by_dim: Dict[str, list] = {}
    for iss in issues:
        dk = iss.get("dimension") or iss.get("issue_type", "other")
        issues_by_dim.setdefault(dk, []).append(iss)

    doc.add_heading("三、问题列表（按维度分类）", level=1)
    if not issues:
        doc.add_paragraph("未发现问题。")
    else:
        global_idx = 1
        all_dim_keys = [d for d in DIM_ORDER if d in issues_by_dim] + \
                       [d for d in issues_by_dim if d not in DIM_ORDER]
        for dk in all_dim_keys:
            dim_issues = issues_by_dim.get(dk, [])
            if not dim_issues:
                continue
            label = DIM_LABELS.get(dk, dk)
            doc.add_heading(f"{label}  （{len(dim_issues)} 处）", level=2)
            tbl = doc.add_table(rows=1, cols=5)
            hdr = tbl.rows[0].cells
            hdr[0].text = "序号"
            hdr[1].text = "严重度"
            hdr[2].text = "原文（页码）"
            hdr[3].text = "建议"
            hdr[4].text = "原因"
            for iss in dim_issues:
                pg  = iss.get("page") or ""
                loc = f"（第{pg}页）" if pg else ""
                sev = SEV_LABELS.get(iss.get("severity", ""), iss.get("severity", ""))
                nr  = " ⚠" if iss.get("needs_review") else ""
                row = tbl.add_row().cells
                row[0].text = str(global_idx)
                row[1].text = sev + nr
                row[2].text = str(iss.get("original", "")) + loc
                row[3].text = str(iss.get("suggestion", ""))
                row[4].text = str(iss.get("reason", ""))
                global_idx += 1
            doc.add_paragraph("")  # spacing between sections

    doc.save(str(out_path))
