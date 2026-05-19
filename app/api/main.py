from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, Optional

from docx import Document
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import AppConfig
from app.exporters import export_review_result
from app.kb.manager import KnowledgeBaseManager
from app.llm.providers import LLMProviderFactory
from app.review.reviewer import Reviewer

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


class LLMConfigPayload(BaseModel):
    provider: str = "mock"
    base_url: str = ""
    api_key: str = ""
    model: str = ""


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


@app.get("/config")
def get_config() -> Dict[str, Any]:
    return config.to_dict()


@app.post("/config")
def save_config(payload: LLMConfigPayload) -> Dict[str, Any]:
    config.llm.provider = payload.provider
    config.llm.base_url = payload.base_url
    config.llm.api_key = payload.api_key
    config.llm.model = payload.model
    config.save()
    return {"ok": True, "message": "LLM 配置已保存", "config": config.to_dict()}


@app.post("/embedding/config")
def save_embedding_config(payload: EmbeddingConfigPayload) -> Dict[str, Any]:
    config.embedding.provider = payload.provider
    config.embedding.base_url = payload.base_url
    config.embedding.api_key = payload.api_key
    config.embedding.model = payload.model
    config.save()
    return {"ok": True, "message": "Embedding 配置已保存", "config": config.to_dict()}


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
) -> Dict[str, Any]:
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        return {"ok": False, "message": "仅支持 PDF 和 DOCX 文件"}

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
        "result": None,
        "error": None,
        "input_path": str(input_path),
    }
    with TASK_LOCK:
        TASKS[task_id] = task

    thread = Thread(
        target=_run_review_task_sync,
        args=(task_id, str(input_path), file.filename, suffix, kb_category, scan_mode, export_markdown, export_csv),
        daemon=True,
    )
    thread.start()
    return {"ok": True, "task_id": task_id, "status": "queued"}


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
    return {
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
    }


def _update_task(task_id: str, **kwargs: Any) -> None:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.update(kwargs)
        task["updated_at"] = datetime.now().isoformat()


def _run_review_task_sync(
    task_id: str,
    input_path: str,
    file_name: str,
    file_type: str,
    kb_category: str,
    scan_mode: bool,
    export_markdown: bool,
    export_csv: bool,
) -> None:
    try:
        _update_task(task_id, status="running", progress=10, message="任务开始执行")
        if file_type == ".pdf":
            _update_task(task_id, progress=25, message="正在解析 PDF / OCR")
        else:
            _update_task(task_id, progress=25, message="正在解析 Word 文档")
        result = _run_review_from_path(input_path, file_name, file_type, kb_category, scan_mode, export_markdown, export_csv, task_id)
        _update_task(task_id, status="completed", progress=100, message="审稿完成", result=result)
    except Exception as e:
        _update_task(task_id, status="failed", progress=100, message="任务失败", error=str(e))


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


def _write_review_docx(out_path: Path, file_name: str, kb_category: str, result: Dict[str, Any]) -> None:
    doc = Document()
    doc.add_heading("AI 审稿报告", level=0)
    doc.add_paragraph(f"原文件：{file_name}")
    doc.add_paragraph(f"知识库类别：{kb_category}")
    doc.add_paragraph(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    doc.add_heading("一、审稿摘要", level=1)
    doc.add_paragraph(result.get("summary", ""))

    doc.add_heading("二、问题列表", level=1)
    issues = result.get("issues", []) or []
    if issues:
        table = doc.add_table(rows=1, cols=5)
        hdr = table.rows[0].cells
        hdr[0].text = "类型"
        hdr[1].text = "严重度"
        hdr[2].text = "原文"
        hdr[3].text = "建议"
        hdr[4].text = "原因"
        for item in issues:
            row = table.add_row().cells
            row[0].text = str(item.get("issue_type", ""))
            row[1].text = str(item.get("severity", ""))
            row[2].text = str(item.get("original", ""))
            row[3].text = str(item.get("suggestion", ""))
            row[4].text = str(item.get("reason", ""))
    else:
        doc.add_paragraph("未发现问题。")

    doc.add_heading("三、修订后文本", level=1)
    doc.add_paragraph(result.get("revised_text", ""))
    doc.save(str(out_path))


def _run_review_from_path(
    input_path: str,
    file_name: str,
    file_type: str,
    kb_category: str,
    scan_mode: bool,
    export_markdown: bool,
    export_csv: bool,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    llm = LLMProviderFactory.from_config(config)
    reviewer = Reviewer(config=config, kb_manager=kb_manager, llm=llm)

    if file_type == ".pdf":
        if task_id:
            _update_task(task_id, progress=45, message="正在恢复 PDF 文本结构")
            _update_task(task_id, progress=70, message="正在检索知识库并执行审稿")
        result = reviewer.review_pdf(file_path=input_path, kb_category=kb_category or None, scan_mode=scan_mode)
    else:
        if task_id:
            _update_task(task_id, progress=45, message="正在提取 Word 文本与表格")
        text = _extract_docx_text(input_path)
        if task_id:
            _update_task(task_id, progress=70, message="正在检索知识库并执行 Word 审稿")
        result = reviewer.review_text(text=text, kb_category=kb_category or None)

    if task_id:
        _update_task(task_id, progress=85, message="正在导出结果")

    exports = {}
    stem = Path(file_name).stem
    if export_markdown:
        md_path = OUTPUT_DIR / f"{stem}_review.md"
        export_review_result(result=result, file_path=str(md_path), mode="markdown")
        exports["markdown"] = str(md_path)
    if export_csv:
        csv_path = OUTPUT_DIR / f"{stem}_issues.csv"
        export_review_result(result=result, file_path=str(csv_path), mode="csv")
        exports["csv"] = str(csv_path)

    output = {
        "summary": getattr(result, "summary", ""),
        "issues": [i.model_dump() if hasattr(i, "model_dump") else dict(i) for i in getattr(result, "issues", [])],
        "revised_text": getattr(result, "revised_text", ""),
        "exports": exports,
        "file_type": file_type,
    }
    save_path = OUTPUT_DIR / f"{stem}_review.json"
    save_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    output["json_path"] = str(save_path)
    return output
