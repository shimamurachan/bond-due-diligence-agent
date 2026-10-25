from __future__ import annotations

import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db, init_database
from app.models import AgentRun, Citation, Document, DocumentChunk, Finding, Job, Project, ProjectNote, ReviewRun, State, new_id, utcnow
from app.services.documents import sha256_bytes
from app.services.demo import build_demo_materials
from app.services.storage import object_store
from app.services.tasks import enqueue, verify_task_request
from app.services.workflow import WorkflowError, answer_project_question, process_job

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT.parent / "web"
templates = Jinja2Templates(directory=str(ROOT / "templates"))
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(init_database)
    yield


app = FastAPI(title="公司债智能尽调工作台", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
if (WEB_ROOT / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(WEB_ROOT / "assets")), name="frontend-assets")


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")
    return project


def _safe_file_name(value: str) -> str:
    name = Path(value).name
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="文件名无效。")
    return re.sub(r"[^\w.\-()\u4e00-\u9fff]+", "_", name)[:180]


def _is_supported(file_name: str, content_type: str, data: bytes) -> bool:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        return True
    if suffix == ".docx" and data.startswith(b"PK"):
        return True
    return content_type in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    } and suffix in {".pdf", ".docx"}


class ProjectCreatePayload(BaseModel):
    name: str = Field(max_length=160)
    material_summary: str = Field(default="", max_length=2000)


class FindingUpdatePayload(BaseModel):
    status: Literal["open", "confirmed", "resolved"]
    note: str = Field(default="", max_length=1000)


class QuestionPayload(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def _when(value) -> str | None:
    return value.isoformat() if value else None


def _project_json(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "material_summary": project.material_summary,
        "created_at": _when(project.created_at),
        "last_active_at": _when(project.last_active_at),
    }


def _document_json(document: Document) -> dict:
    return {
        "id": document.id,
        "file_name": document.file_name,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "source_note": document.source_note,
        "status": document.status,
        "parser_summary": document.parser_summary,
        "extraction_error": document.extraction_error,
        "indexed_at": _when(document.indexed_at),
        "created_at": _when(document.created_at),
    }


def _job_json(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "document_id": job.document_id,
        "status": job.status,
        "error": job.error_message,
        "created_at": _when(job.created_at),
        "completed_at": _when(job.completed_at),
    }


def _project_detail_json(db: Session, project_id: str) -> dict:
    project = _project_or_404(db, project_id)
    documents = list(db.scalars(select(Document).where(Document.project_id == project_id).order_by(Document.created_at.desc())))
    jobs = list(db.scalars(select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(20)))
    latest = db.scalar(select(ReviewRun).where(ReviewRun.project_id == project_id).order_by(ReviewRun.created_at.desc()))
    findings: list[dict] = []
    agents: list[dict] = []
    review: dict | None = None
    if latest:
        review = {
            "id": latest.id,
            "status": latest.status,
            "summary": latest.summary,
            "material_count": latest.material_count,
            "created_at": _when(latest.created_at),
            "completed_at": _when(latest.completed_at),
        }
        for finding in db.scalars(select(Finding).where(Finding.review_run_id == latest.id).order_by(Finding.severity, Finding.created_at)):
            citations = list(db.scalars(select(Citation).where(Citation.finding_id == finding.id)))
            findings.append({
                "id": finding.id,
                "severity": finding.severity,
                "title": finding.title,
                "rule_code": finding.rule_code,
                "evidence": finding.evidence,
                "recommendation": finding.recommendation,
                "confidence": finding.confidence,
                "status": finding.status,
                "change_status": finding.change_status,
                "review_note": finding.review_note,
                "citations": [{
                    "file_name": citation.file_name,
                    "location": citation.location,
                    "excerpt": citation.excerpt,
                } for citation in citations],
            })
        agents = [{
            "agent_name": agent.agent_name,
            "status": agent.status,
            "input_summary": agent.input_summary,
            "output_summary": agent.output_summary,
            "error": agent.error_message,
            "duration_ms": agent.duration_ms,
        } for agent in db.scalars(select(AgentRun).where(AgentRun.review_run_id == latest.id).order_by(AgentRun.created_at))]
    notes = [{"kind": note.kind, "content": note.content, "created_at": _when(note.created_at)} for note in db.scalars(
        select(ProjectNote).where(ProjectNote.project_id == project_id).order_by(ProjectNote.created_at.desc()).limit(20)
    )]
    return {
        "project": _project_json(project),
        "documents": [_document_json(document) for document in documents],
        "jobs": [_job_json(job) for job in jobs],
        "review": review,
        "findings": findings,
        "agents": agents,
        "notes": notes,
    }


def _create_demo(db: Session, background_tasks: BackgroundTasks) -> Project:
    project = Project(
        name="试用数据：远景实业 2026 年公司债",
        material_summary="系统生成的脱敏模拟材料，用于体验完整 OCR/RAG/多 Agent 流程。",
    )
    db.add(project)
    db.flush()
    stored_keys: list[str] = []
    jobs: list[Job] = []
    try:
        for material in build_demo_materials():
            document_id = new_id()
            storage_key = f"projects/{project.id}/{document_id}/{material.file_name}"
            object_store.put(storage_key, material.content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            stored_keys.append(storage_key)
            document = Document(
                id=document_id,
                project_id=project.id,
                file_name=material.file_name,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                storage_key=storage_key,
                size_bytes=len(material.content),
                sha256=sha256_bytes(material.content),
                source_note=material.source_note,
            )
            job = Job(kind="ingest", project_id=project.id, document_id=document_id)
            db.add_all([document, job])
            jobs.append(job)
        db.commit()
    except Exception:
        for key in stored_keys:
            object_store.delete(key)
        db.rollback()
        raise
    try:
        for job in jobs:
            enqueue(job.id, background_tasks)
    except Exception as exc:
        for job in jobs:
            job.status = State.FAILED.value
            job.error_message = f"试用材料任务创建失败：{exc}"[:1500]
        db.commit()
        raise HTTPException(status_code=503, detail="试用材料已保存，但后台任务创建失败，请稍后重试。") from exc
    return project


@app.get("/api/projects")
def list_projects(db: Session = Depends(get_db)):
    projects = list(db.scalars(select(Project).order_by(Project.last_active_at.desc())))
    return {"projects": [_project_json(project) for project in projects]}


@app.post("/api/projects", status_code=201)
def api_create_project(payload: ProjectCreatePayload, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请输入项目名称。")
    project = Project(name=name, material_summary=payload.material_summary.strip())
    db.add(project)
    db.commit()
    return {"project": _project_json(project)}


@app.post("/api/projects/demo", status_code=201)
def api_create_demo_project(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _create_demo(db, background_tasks)
    return {"project": _project_json(project)}


@app.get("/api/projects/{project_id}")
def get_project_detail(project_id: str, db: Session = Depends(get_db)):
    return _project_detail_json(db, project_id)


@app.post("/api/projects/{project_id}/documents", status_code=202)
async def api_upload_document(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_note: str = Form("用户上传"),
    db: Session = Depends(get_db),
):
    project = _project_or_404(db, project_id)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="不能上传空文件。")
    if len(data) > settings.max_file_bytes:
        raise HTTPException(status_code=413, detail=f"单个文件不能超过 {settings.max_file_size_mb}MB。")
    name = _safe_file_name(file.filename or "upload")
    content_type = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not _is_supported(name, content_type, data):
        raise HTTPException(status_code=400, detail="仅支持有效的 PDF 或 DOCX 文件。")
    document_id = new_id()
    storage_key = f"projects/{project_id}/{document_id}/{name}"
    object_store.put(storage_key, data, content_type)
    document = Document(
        id=document_id, project_id=project_id, file_name=name, content_type=content_type,
        storage_key=storage_key, size_bytes=len(data), sha256=sha256_bytes(data),
        source_note=source_note.strip()[:500] or "用户上传",
    )
    job = Job(kind="ingest", project_id=project_id, document_id=document_id)
    project.last_active_at = utcnow()
    db.add_all([document, job])
    db.commit()
    try:
        enqueue(job.id, background_tasks)
    except Exception:
        object_store.delete(storage_key)
        db.delete(document)
        db.delete(job)
        db.commit()
        raise
    return {"document": _document_json(document), "job": _job_json(job)}


@app.post("/api/projects/{project_id}/reviews", status_code=202)
def api_start_review(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    if not list(db.scalars(select(Document.id).where(Document.project_id == project_id))):
        raise HTTPException(status_code=400, detail="请先上传材料。")
    review = ReviewRun(project_id=project.id, source_snapshot={})
    db.add(review)
    db.flush()
    job = Job(kind="review", project_id=project.id, payload={"review_run_id": review.id})
    project.last_active_at = utcnow()
    db.add(job)
    db.commit()
    enqueue(job.id, background_tasks)
    return {"review_id": review.id, "job": _job_json(job)}


@app.delete("/api/projects/{project_id}", status_code=204)
def api_delete_project(project_id: str, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    for document in db.scalars(select(Document).where(Document.project_id == project_id)):
        object_store.delete(document.storage_key)
    db.delete(project)
    db.commit()


@app.delete("/api/projects/{project_id}/documents/{document_id}", status_code=204)
def api_delete_document(project_id: str, document_id: str, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    document = db.get(Document, document_id)
    if not document or document.project_id != project_id:
        raise HTTPException(status_code=404, detail="文件不存在。")
    active_job = db.scalar(select(Job.id).where(Job.document_id == document_id, Job.status.in_([State.QUEUED.value, State.RUNNING.value])))
    if active_job:
        raise HTTPException(status_code=409, detail="该文件正在处理，处理结束后才能删除。")
    cited = db.scalar(select(Citation.id).join(DocumentChunk, Citation.chunk_id == DocumentChunk.id).where(DocumentChunk.document_id == document_id).limit(1))
    if cited:
        raise HTTPException(status_code=409, detail="该文件已被历史审阅引用。请删除整个项目以彻底清除材料。")
    object_store.delete(document.storage_key)
    db.delete(document)
    db.commit()


@app.patch("/api/findings/{finding_id}")
def api_update_finding(finding_id: str, payload: FindingUpdatePayload, db: Session = Depends(get_db)):
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="风险项不存在。")
    finding.status = payload.status
    finding.review_note = payload.note.strip()
    db.commit()
    return {"id": finding.id, "status": finding.status, "review_note": finding.review_note}


@app.post("/api/projects/{project_id}/questions")
def api_ask_question(project_id: str, payload: QuestionPayload, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    try:
        return answer_project_question(db, project_id, payload.question.strip())
    except WorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _frontend_response(request: Request):
    index = WEB_ROOT / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    # Preserve a readable development fallback if the frontend has not been built.
    projects = []
    with SessionLocal() as db:
        projects = list(db.scalars(select(Project).order_by(Project.last_active_at.desc())))
    return templates.TemplateResponse(request, "index.html", {"projects": projects, "settings": settings})


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _frontend_response(request)


@app.post("/projects")
def create_project(name: str = Form(...), material_summary: str = Form(""), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请输入项目名称。")
    project = Project(name=name[:160], material_summary=material_summary.strip()[:2000])
    db.add(project)
    db.commit()
    return _redirect(f"/projects/{project.id}")


@app.post("/projects/demo")
def create_demo_project(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create a standalone project so trial data never mixes with user material."""
    project = _create_demo(db, background_tasks)
    return _redirect(f"/projects/{project.id}")


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(project_id: str, request: Request):
    return _frontend_response(request)


@app.post("/projects/{project_id}/documents")
async def upload_document(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_note: str = Form("用户上传"),
    db: Session = Depends(get_db),
):
    project = _project_or_404(db, project_id)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="不能上传空文件。")
    if len(data) > settings.max_file_bytes:
        raise HTTPException(status_code=413, detail=f"单个文件不能超过 {settings.max_file_size_mb}MB。")
    name = _safe_file_name(file.filename or "upload")
    content_type = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not _is_supported(name, content_type, data):
        raise HTTPException(status_code=400, detail="仅支持有效的 PDF 或 DOCX 文件。")
    document_id = new_id()
    storage_key = f"projects/{project_id}/{document_id}/{name}"
    object_store.put(storage_key, data, content_type)
    document = Document(
        id=document_id,
        project_id=project_id,
        file_name=name,
        content_type=content_type,
        storage_key=storage_key,
        size_bytes=len(data),
        sha256=sha256_bytes(data),
        source_note=source_note.strip()[:500] or "用户上传",
    )
    job = Job(kind="ingest", project_id=project_id, document_id=document_id)
    project.last_active_at = utcnow()
    db.add_all([document, job])
    db.commit()
    try:
        enqueue(job.id, background_tasks)
    except Exception:
        object_store.delete(storage_key)
        db.delete(document)
        db.delete(job)
        db.commit()
        raise
    return _redirect(f"/projects/{project_id}")


@app.post("/projects/{project_id}/reviews")
def start_review(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    if not list(db.scalars(select(Document.id).where(Document.project_id == project_id))):
        raise HTTPException(status_code=400, detail="请先上传材料。")
    review = ReviewRun(project_id=project_id, source_snapshot={})
    db.add(review)
    db.flush()
    job = Job(kind="review", project_id=project_id, payload={"review_run_id": review.id})
    project.last_active_at = utcnow()
    db.add(job)
    db.commit()
    enqueue(job.id, background_tasks)
    return _redirect(f"/projects/{project_id}")


@app.post("/projects/{project_id}/delete")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    documents = list(db.scalars(select(Document).where(Document.project_id == project_id)))
    for document in documents:
        object_store.delete(document.storage_key)
    db.delete(project)
    db.commit()
    return _redirect("/")


@app.post("/projects/{project_id}/documents/{document_id}/delete")
def delete_document(project_id: str, document_id: str, db: Session = Depends(get_db)):
    """Delete only material that is not already cited by a completed review.

    Cited source material is deliberately retained: silently deleting it would turn
    an otherwise auditable historical risk item into an unverifiable assertion.
    Users who need to erase all material can delete the whole project instead.
    """
    _project_or_404(db, project_id)
    document = db.get(Document, document_id)
    if not document or document.project_id != project_id:
        raise HTTPException(status_code=404, detail="文件不存在。")
    active_job = db.scalar(
        select(Job.id).where(
            Job.document_id == document_id,
            Job.status.in_([State.QUEUED.value, State.RUNNING.value]),
        )
    )
    if active_job:
        raise HTTPException(status_code=409, detail="该文件正在处理，处理结束后才能删除。")
    cited = db.scalar(
        select(Citation.id)
        .join(DocumentChunk, Citation.chunk_id == DocumentChunk.id)
        .where(DocumentChunk.document_id == document_id)
        .limit(1)
    )
    if cited:
        raise HTTPException(
            status_code=409,
            detail="该文件已被历史审阅引用。为保留证据链，不能单独删除；如需彻底清除，请删除整个项目。",
        )
    object_store.delete(document.storage_key)
    db.delete(document)
    db.commit()
    return _redirect(f"/projects/{project_id}")


@app.post("/findings/{finding_id}/confirm")
def confirm_finding(finding_id: str, status: str = Form(...), note: str = Form(""), db: Session = Depends(get_db)):
    finding = db.get(Finding, finding_id)
    if not finding or status not in {"open", "confirmed", "resolved"}:
        raise HTTPException(status_code=400, detail="风险状态无效。")
    finding.status = status
    finding.review_note = note.strip()[:1000]
    db.commit()
    review = db.get(ReviewRun, finding.review_run_id)
    return _redirect(f"/projects/{review.project_id}")


@app.post("/projects/{project_id}/questions")
def ask_question(project_id: str, question: str = Form(...), db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    if not question.strip():
        raise HTTPException(status_code=400, detail="请输入问题。")
    try:
        answer_project_question(db, project_id, question.strip())
    except WorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _redirect(f"/projects/{project_id}")


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return JSONResponse({"id": job.id, "kind": job.kind, "status": job.status, "error": job.error_message})


@app.post("/internal/tasks/{job_id}")
async def execute_task(job_id: str, request: Request):
    await verify_task_request(request)
    await run_in_threadpool(process_job, job_id)
    return {"job_id": job_id, "status": "processed"}


@app.post("/internal/maintenance/purge")
async def purge_expired_projects(request: Request):
    """Cloud Scheduler entrypoint; authenticated like Cloud Tasks."""
    await verify_task_request(request)
    with SessionLocal() as db:
        job = Job(kind="purge")
        db.add(job)
        db.commit()
        job_id = job.id
    await run_in_threadpool(process_job, job_id)
    return {"job_id": job_id, "status": "processed"}


@app.get("/healthz")
def healthcheck():
    return {"status": "ok", "environment": settings.app_env}
