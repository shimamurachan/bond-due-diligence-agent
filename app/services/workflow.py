from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    AgentRun,
    Citation,
    Document,
    DocumentChunk,
    DocumentPage,
    Finding,
    Job,
    Project,
    ProjectNote,
    ReviewRun,
    State,
    new_id,
    utcnow,
)
from app.services.documents import DocumentParser, build_chunks
from app.services.embeddings import embedding_client
from app.services.llm import (
    AnswerResult,
    ExtractionResult,
    RetrievalResult,
    ReviewPlan,
    ReviewResult,
    llm_client,
)
from app.services.storage import object_store

AGENTS = ("流程编排 Agent", "材料抽取 Agent", "证据检索 Agent", "风险审阅 Agent")


class WorkflowError(RuntimeError):
    pass


def process_job(job_id: str) -> None:
    """Cloud Tasks and local BackgroundTasks use the same idempotent worker entrypoint."""
    with SessionLocal() as db:
        # Cloud Tasks is at-least-once. Lock the row and let exactly one worker
        # move a queued job to running; a duplicate delivery then becomes a no-op.
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.status != State.QUEUED.value:
            return
        job.status = State.RUNNING.value
        job.started_at = utcnow()
        job.error_message = ""
        db.commit()
        try:
            if job.kind == "ingest":
                _ingest_document(db, job)
            elif job.kind == "review":
                _run_review(db, job)
            elif job.kind == "purge":
                _purge_expired_projects(db)
            else:
                raise WorkflowError(f"未知任务类型：{job.kind}")
            job.status = State.COMPLETED.value
            job.completed_at = utcnow()
        except Exception as exc:
            job.status = State.FAILED.value
            job.completed_at = utcnow()
            job.error_message = str(exc)[:1500]
            if job.document_id:
                document = db.get(Document, job.document_id)
                if document:
                    document.status = State.FAILED.value
                    document.extraction_error = job.error_message
            if job.kind == "review" and (review_id := job.payload.get("review_run_id")):
                review = db.get(ReviewRun, review_id)
                if review:
                    review.status = State.FAILED.value
                    _fail_active_agent(db, review.id, job.error_message)
        finally:
            db.commit()


def _ingest_document(db: Session, job: Job) -> None:
    document = db.get(Document, job.document_id)
    if not document:
        raise WorkflowError("上传文件不存在。")
    project = db.get(Project, document.project_id)
    if not project:
        raise WorkflowError("项目不存在。")
    document.status = State.RUNNING.value
    db.commit()
    parser = DocumentParser()
    parsed_pages = parser.parse(document.file_name, document.content_type, object_store.read(document.storage_key))
    chunks = build_chunks(parsed_pages)
    if not chunks:
        raise WorkflowError("文件未生成可检索文本。")
    vectors = embedding_client.embed_documents([chunk.content for chunk in chunks])
    if len(vectors) != len(chunks):
        raise WorkflowError("向量数量与文本分块不一致。")
    # The first indexing attempt is immutable. A later reprocess should upload a new source
    # version, preserving citations attached to completed reviews.
    db.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    for page in parsed_pages:
        db.add(DocumentPage(
            document_id=document.id,
            project_id=document.project_id,
            page_number=page.page_number,
            extraction_method=page.extraction_method,
            content=page.content,
            ocr_confidence=page.ocr_confidence,
        ))
    for chunk, vector in zip(chunks, vectors, strict=True):
        db.add(DocumentChunk(
            document_id=document.id,
            project_id=document.project_id,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            location=chunk.location,
            structure_type=chunk.structure_type,
            content=chunk.content,
            search_terms=chunk.search_terms,
            embedding=vector,
            embedding_model=embedding_client.model_name,
            content_hash=chunk.content_hash,
        ))
    method_counts: dict[str, int] = defaultdict(int)
    for page in parsed_pages:
        method_counts[page.extraction_method] += 1
    document.status = State.READY.value
    document.indexed_at = utcnow()
    document.parser_summary = json.dumps({"pages": len(parsed_pages), "chunks": len(chunks), "methods": method_counts}, ensure_ascii=False)
    document.extraction_error = ""
    project.last_active_at = utcnow()


def _run_review(db: Session, job: Job) -> None:
    review = db.get(ReviewRun, job.payload.get("review_run_id"))
    if not review:
        raise WorkflowError("审阅批次不存在。")
    project = db.get(Project, review.project_id)
    documents = list(db.scalars(select(Document).where(Document.project_id == review.project_id).order_by(Document.created_at)))
    if not documents:
        raise WorkflowError("请先上传至少一份材料。")
    pending = [document.file_name for document in documents if document.status != State.READY.value]
    if pending:
        raise WorkflowError(f"以下材料尚未完成解析或索引：{'、'.join(pending)}")
    chunks = list(db.scalars(select(DocumentChunk).where(DocumentChunk.project_id == review.project_id).order_by(DocumentChunk.created_at)))
    if not chunks:
        raise WorkflowError("材料没有可检索文本。")
    review.status = State.RUNNING.value
    review.material_count = len(documents)
    review.source_snapshot = {document.id: document.sha256 for document in documents}
    project.last_active_at = utcnow()
    db.commit()

    _seed_agents(db, review.id)
    memories = list(db.scalars(
        select(ProjectNote).where(ProjectNote.project_id == review.project_id).order_by(ProjectNote.created_at.desc()).limit(10)
    ))
    plan = _agent(
        db, review.id, AGENTS[0],
        f"材料：{'、'.join(document.file_name for document in documents)}",
        lambda: llm_client.structured(
            "review_plan",
            "你是公司债尽调流程编排 Agent。根据材料文件名制定本轮核查计划，重点覆盖募集资金用途、偿债能力、担保与增信。只输出核查目标和 3-6 条具体检索问题，不得自行判断风险。",
            {"materials": [document.file_name for document in documents], "project_memory": [note.content for note in reversed(memories)]},
            ReviewPlan,
        ),
    )
    extraction = _agent(
        db, review.id, AGENTS[1],
        f"材料片段：{len(chunks)} 条",
        lambda: llm_client.structured(
            "material_extraction",
            "你是公司债尽调材料抽取 Agent。仅根据所给材料提取发行主体、募集资金用途、财务与偿债指标、担保增信和重大事项。每条事实必须填写 evidence_chunk_id；没有依据时不得推测。",
            {"chunks": [_as_context(chunk, limit=1200) for chunk in chunks[:120]]},
            ExtractionResult,
        ),
    )
    chunk_by_id = {chunk.id: chunk for chunk in chunks}
    if any(fact.evidence_chunk_id not in chunk_by_id for fact in extraction.facts):
        raise WorkflowError("材料抽取返回了无效证据引用。")

    candidates: dict[str, dict] = {}
    for query in plan.queries:
        for candidate in hybrid_search(db, review.project_id, query, limit=12):
            candidates[candidate["id"]] = candidate
    if not candidates:
        raise WorkflowError("混合检索没有找到可用证据。")
    retrieval = _agent(
        db, review.id, AGENTS[2],
        f"查询：{'；'.join(plan.queries)}",
        lambda: llm_client.structured(
            "evidence_retrieval",
            "你是公司债尽调证据检索 Agent。仅从候选证据中选择最能回答核查问题的片段 ID。不得选择候选外的 ID，不得作出风险结论。",
            {"queries": plan.queries, "candidates": list(candidates.values())[:24]},
            RetrievalResult,
        ),
    )
    if not retrieval.selected_chunk_ids or any(key not in candidates for key in retrieval.selected_chunk_ids):
        raise WorkflowError("证据检索返回了无效的当前项目引用。")
    evidence = [candidates[key] for key in retrieval.selected_chunk_ids]
    result = _agent(
        db, review.id, AGENTS[3],
        f"证据：{len(evidence)} 条",
        lambda: llm_client.structured(
            "risk_review",
            "你是公司债尽调风险审阅 Agent。只依据提供证据评估募集资金用途一致性、偿债能力和担保增信完整性。每个风险项必须填写 evidence_chunk_id；无充足依据时 findings 为空，并在 summary 写明“证据不足”。禁止使用外部知识、编造数据或页码。",
            {
                "objective": plan.objective,
                "issuer": extraction.issuer,
                "facts": [fact.model_dump() for fact in extraction.facts],
                "evidence": evidence,
            },
            ReviewResult,
        ),
    )
    if any(item.evidence_chunk_id not in candidates for item in result.findings):
        raise WorkflowError("风险审阅返回了无效证据引用。")
    _save_findings(db, review, result, candidates)
    review.status = State.COMPLETED.value
    review.summary = result.summary
    review.completed_at = utcnow()
    db.add(ProjectNote(project_id=review.project_id, kind="answer", content=f"审阅摘要：{result.summary}"))


def hybrid_search(db: Session, project_id: str, query: str, limit: int = 12) -> list[dict]:
    vector = embedding_client.embed_query(query)
    vector_literal = "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
    semantic = db.execute(text("""
        SELECT c.id, c.document_id, c.page_start, c.page_end, c.location, c.content,
               1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
        FROM document_chunks AS c
        JOIN documents AS d ON d.id = c.document_id
        WHERE c.project_id = :project_id AND d.status = 'ready' AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """), {"embedding": vector_literal, "project_id": project_id, "limit": limit}).mappings().all()
    from app.services.documents import tokenise_for_search
    terms = tokenise_for_search(query)
    lexical = db.execute(text("""
        WITH search_query AS (SELECT websearch_to_tsquery('simple', :terms) AS value)
        SELECT c.id, c.document_id, c.page_start, c.page_end, c.location, c.content,
               ts_rank_cd(c.search_vector, search_query.value) AS score
        FROM document_chunks AS c
        JOIN documents AS d ON d.id = c.document_id, search_query
        WHERE c.project_id = :project_id AND d.status = 'ready' AND c.search_vector @@ search_query.value
        ORDER BY ts_rank_cd(c.search_vector, search_query.value) DESC
        LIMIT :limit
    """), {"terms": terms, "project_id": project_id, "limit": limit}).mappings().all()
    fused: dict[str, dict] = {}
    for rank, row in enumerate(semantic, start=1):
        item = dict(row)
        item["rrf"] = item.get("rrf", 0) + 1 / (60 + rank)
        fused[item["id"]] = item
    for rank, row in enumerate(lexical, start=1):
        item = fused.setdefault(row["id"], dict(row))
        item["rrf"] = item.get("rrf", 0) + 1 / (60 + rank)
    return sorted(fused.values(), key=lambda item: item["rrf"], reverse=True)[:limit]


def answer_project_question(db: Session, project_id: str, question: str) -> dict:
    evidence = hybrid_search(db, project_id, question, limit=8)
    if not evidence:
        raise WorkflowError("当前项目没有可引用的材料证据。")
    answer = llm_client.structured(
        "project_answer",
        "你是公司债尽调项目助理。只能依据当前项目提供的证据回答问题；证据不足时直接说明。source_chunk_ids 只能选择提供的证据 ID。",
        {"question": question, "evidence": evidence},
        AnswerResult,
    )
    valid_ids = [chunk_id for chunk_id in answer.source_chunk_ids if chunk_id in {item["id"] for item in evidence}]
    if len(valid_ids) != len(answer.source_chunk_ids):
        raise WorkflowError("项目追问返回了无效引用。")
    db.add(ProjectNote(project_id=project_id, kind="question", content=question))
    db.add(ProjectNote(project_id=project_id, kind="answer", content=answer.answer))
    db.commit()
    return {"answer": answer.answer, "sources": [item for item in evidence if item["id"] in valid_ids]}


def _seed_agents(db: Session, review_id: str) -> None:
    if db.scalar(select(AgentRun.id).where(AgentRun.review_run_id == review_id)):
        return
    for name in AGENTS:
        db.add(AgentRun(review_run_id=review_id, agent_name=name, status=State.QUEUED.value))
    db.commit()


def _agent(db: Session, review_id: str, name: str, input_summary: str, call):
    agent = db.scalar(select(AgentRun).where(AgentRun.review_run_id == review_id, AgentRun.agent_name == name))
    if not agent:
        raise WorkflowError(f"缺少 Agent 运行轨迹：{name}")
    agent.status = State.RUNNING.value
    agent.input_summary = input_summary[:900]
    db.commit()
    started = time.perf_counter()
    try:
        result = call()
    except Exception as exc:
        agent.status = State.FAILED.value
        agent.error_message = str(exc)[:1500]
        agent.duration_ms = int((time.perf_counter() - started) * 1000)
        db.commit()
        raise
    agent.status = State.COMPLETED.value
    agent.output_summary = json.dumps(result.model_dump(), ensure_ascii=False)[:900]
    agent.duration_ms = int((time.perf_counter() - started) * 1000)
    db.commit()
    return result


def _save_findings(db: Session, review: ReviewRun, result: ReviewResult, candidates: dict[str, dict]) -> None:
    previous = db.scalar(
        select(ReviewRun)
        .where(ReviewRun.project_id == review.project_id, ReviewRun.status == State.COMPLETED.value, ReviewRun.id != review.id)
        .order_by(ReviewRun.completed_at.desc())
    )
    previous_by_code: dict[str, Finding] = {}
    if previous:
        previous_by_code = {item.rule_code: item for item in db.scalars(select(Finding).where(Finding.review_run_id == previous.id))}
    current_codes: set[str] = set()
    for proposed in result.findings:
        current_codes.add(proposed.rule_code)
        earlier = previous_by_code.get(proposed.rule_code)
        change = "new" if not earlier else (
            "unchanged" if earlier.severity == proposed.severity and earlier.title == proposed.title and earlier.evidence == proposed.evidence else "changed"
        )
        finding = Finding(
            review_run_id=review.id,
            rule_code=proposed.rule_code,
            severity=proposed.severity,
            title=proposed.title,
            evidence=proposed.evidence,
            recommendation=proposed.recommendation,
            confidence=proposed.confidence,
            change_status=change,
        )
        db.add(finding)
        db.flush()
        source = candidates[proposed.evidence_chunk_id]
        db.add(Citation(
            finding_id=finding.id,
            chunk_id=source["id"],
            file_name=_document_name(db, source["document_id"]),
            location=source["location"],
            excerpt=source["content"][:1000],
        ))
    for code, earlier in previous_by_code.items():
        if code not in current_codes:
            db.add(Finding(
                review_run_id=review.id,
                rule_code=code,
                severity=earlier.severity,
                title=earlier.title,
                evidence="上一版存在该风险，本轮材料未再次支持该结论。",
                recommendation="请人工确认新增材料是否已充分解除该风险。",
                confidence=earlier.confidence,
                status="resolved",
                change_status="resolved",
            ))
    db.commit()


def _document_name(db: Session, document_id: str) -> str:
    document = db.get(Document, document_id)
    return document.file_name if document else "已删除材料"


def _as_context(chunk: DocumentChunk, limit: int = 1600) -> dict:
    return {"id": chunk.id, "location": chunk.location, "content": chunk.content[:limit]}


def _fail_active_agent(db: Session, review_id: str, message: str) -> None:
    agent = db.scalar(select(AgentRun).where(AgentRun.review_run_id == review_id, AgentRun.status == State.RUNNING.value))
    if agent:
        agent.status = State.FAILED.value
        agent.error_message = message[:1500]


def _purge_expired_projects(db: Session) -> None:
    settings = get_settings()
    cutoff = utcnow() - timedelta(days=settings.project_retention_days)
    projects = list(db.scalars(select(Project).where(Project.last_active_at < cutoff)))
    for project in projects:
        for document in db.scalars(select(Document).where(Document.project_id == project.id)):
            object_store.delete(document.storage_key)
        db.delete(project)
