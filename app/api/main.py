from __future__ import annotations

import asyncio
import json
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


@app.post("/tasks/review_document")
async def create_review_task(
    file: UploadFile = File(...),
    kb_category: str = Form(default=""),
    scan_mode: bool = Form(default=False),
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
    input_path = INPUT_DIR / f"{task_id}_{file.filename}"
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
              scan_mode, export_markdown, export_csv, selected_dims, p_start, p_end),
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
            _doc = _fitz.open(input_path)
            total_pages = len(_doc)
            # Resolve page range (1-based, inclusive)
            p0 = max(0, page_start - 1)          # convert to 0-based
            p1 = (page_end if page_end > 0 else total_pages)  # exclusive end
            p1 = min(p1, total_pages)
            page_range_desc = (f"第{p0+1}-{p1}页" if p1 < total_pages or p0 > 0
                               else f"全文共{total_pages}页")
            _update_task(task_id, progress=10,
                         message=f"正在解析 PDF 文本（{page_range_desc}，共{total_pages}页）")

            text_parts = []
            for pi in range(p0, p1):
                page_text = _doc[pi].get_text()
                text_parts.append(page_text)
                for li, line in enumerate(page_text.split('\n'), 1):
                    if line.strip():
                        key = line.strip()[:50]
                        if key not in page_line_index:
                            page_line_index[key] = {"page": pi + 1, "line": li}
            _doc.close()
            text = "\n".join(text_parts)

            _emit_task_event(task_id, {
                "type": "doc_parsed",
                "source_text": text[:60000],
                "text_length": len(text),
                "total_chunks": max(1, len(text) // 2000 + 1),
                "page_start": p0 + 1,
                "page_end": p1,
                "total_pages": total_pages,
                "message": f"PDF 解析完成：{page_range_desc}，共 {len(text):,} 字",
            })
        else:
            _update_task(task_id, progress=10, message="正在提取 Word 文本")
            text = _extract_docx_text(input_path)
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
        _update_task(task_id, source_text=source_preview, progress=12)

        if _is_cancelled(task_id):
            raise TaskCancelledException()

        # ── 2. 两阶段并行审稿（Phase1/Phase2 始终使用各自独立配置）──────────────
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
            chunk_size=2000,
        )

        def event_callback(event_type: str, **kwargs) -> None:
            if _is_cancelled(task_id):
                raise TaskCancelledException()
            _emit_task_event(task_id, {"type": event_type, **kwargs})
            # Keep legacy progress field in sync
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
        )

        if _is_cancelled(task_id):
            raise TaskCancelledException()

        result.source_file = input_path
        result.source_text = source_preview

        # ── 3. 匹配页码/行号 ───────────────────────────────────────────────────
        for issue in result.issues:
            if page_line_index and issue.original:
                # Try progressively shorter keys to find a match
                for length in (50, 30, 20, 15):
                    key = issue.original[:length]
                    if key in page_line_index:
                        pos = page_line_index[key]
                        issue.page = pos["page"]
                        break

        # ── 4. 序列化问题列表（含页码行号）────────────────────────────────────
        issues_dicts = [
            i.model_dump() if hasattr(i, "model_dump") else dict(i)
            for i in result.issues
        ]
        # Enrich with display_location field for UI
        for iss in issues_dicts:
            pg = iss.get("page") or ""
            iss["display_location"] = f"第 {pg} 页" if pg else ""

        # ── 5. 导出文件（始终保存 JSON + TXT 到 output，可选 md/csv）────────────
        _update_task(task_id, progress=93, message="正在导出结果")
        exports: Dict[str, str] = {}
        stem = Path(file_name).stem
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
            "source_text": source_preview,
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
    }
    DIM_ORDER = ["grammar", "spelling", "style", "terminology", "logic", "fact", "qa_check"]

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

    # Per-dimension sections
    global_idx = 1
    for dim_key in DIM_ORDER:
        dim_issues = issues_by_dim.get(dim_key, [])
        if not dim_issues:
            continue
        label = DIM_MAP.get(dim_key, dim_key)
        lines += [
            "",
            f"{'=' * 65}",
            f"  {label}  （{len(dim_issues)} 处）",
            f"{'=' * 65}",
        ]
        for iss in dim_issues:
            pg  = iss.get("page") or ""
            loc = f"第 {pg} 页  " if pg else ""
            sev = SEV_MAP.get(iss.get("severity", ""), iss.get("severity", ""))
            nr  = "  ⚠需人工复核" if iss.get("needs_review") else ""
            lines.append(f"\n[{global_idx:3d}]  {loc}[{sev}]{nr}")
            lines.append(f"  原文：{iss.get('original', '')}")
            lines.append(f"  建议：{iss.get('suggestion', '')}")
            lines.append(f"  原因：{iss.get('reason', '')}")
            global_idx += 1

    # Any unexpected dimensions not in DIM_ORDER
    for dim_key, dim_issues in issues_by_dim.items():
        if dim_key in DIM_ORDER or not dim_issues:
            continue
        lines += ["", f"{'=' * 65}", f"  {dim_key}  （{len(dim_issues)} 处）", f"{'=' * 65}"]
        for iss in dim_issues:
            pg  = iss.get("page") or ""
            loc = f"第 {pg} 页  " if pg else ""
            sev = SEV_MAP.get(iss.get("severity", ""), iss.get("severity", ""))
            lines.append(f"\n[{global_idx:3d}]  {loc}[{sev}]")
            lines.append(f"  原文：{iss.get('original', '')}")
            lines.append(f"  建议：{iss.get('suggestion', '')}")
            lines.append(f"  原因：{iss.get('reason', '')}")
            global_idx += 1

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
