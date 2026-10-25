from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google.cloud import tasks_v2

from app.config import Settings, get_settings
from app.services.workflow import process_job


def enqueue(job_id: str, background_tasks: BackgroundTasks, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.is_cloud:
        background_tasks.add_task(process_job, job_id)
        return
    if not settings.tasks_service_url or not settings.tasks_invoker_service_account:
        raise RuntimeError("Cloud Run 环境缺少 TASKS_SERVICE_URL 或 TASKS_INVOKER_SERVICE_ACCOUNT。")
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(settings.google_cloud_project, settings.google_cloud_region, settings.task_queue)
    endpoint = f"{settings.tasks_service_url.rstrip('/')}/internal/tasks/{job_id}"
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": endpoint,
            "oidc_token": {
                "service_account_email": settings.tasks_invoker_service_account,
                "audience": settings.tasks_service_url.rstrip("/"),
            },
        }
    }
    client.create_task(request={"parent": parent, "task": task})


async def verify_task_request(request: Request, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.is_cloud:
        if request.headers.get("X-Task-Secret") != settings.tasks_shared_secret:
            raise HTTPException(status_code=403, detail="任务调用未授权。")
        return
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="缺少 Cloud Tasks 身份令牌。")
    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=settings.tasks_service_url.rstrip("/"),
        )
        if claims.get("email") != settings.tasks_invoker_service_account:
            raise ValueError("unexpected task service account")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Cloud Tasks 身份令牌无效。") from exc
