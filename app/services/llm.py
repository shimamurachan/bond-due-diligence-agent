from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, Field

from app.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)


class LlmError(RuntimeError):
    pass


class ReviewPlan(BaseModel):
    objective: str
    queries: list[str] = Field(min_length=3, max_length=6)


class ExtractedFact(BaseModel):
    category: str
    fact: str
    evidence_chunk_id: str


class ExtractionResult(BaseModel):
    issuer: str = ""
    facts: list[ExtractedFact]


class RetrievalResult(BaseModel):
    selected_chunk_ids: list[str]
    rationale: str


class ProposedFinding(BaseModel):
    severity: str
    title: str
    rule_code: str
    evidence_chunk_id: str
    evidence: str
    recommendation: str
    confidence: float = Field(ge=0, le=1)


class ReviewResult(BaseModel):
    summary: str
    findings: list[ProposedFinding]


class AnswerResult(BaseModel):
    answer: str
    source_chunk_ids: list[str]


class DeepSeekClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def structured(self, name: str, instructions: str, content: object, result_type: type[T]) -> T:
        if not self.settings.deepseek_api_key:
            raise LlmError("未配置 DEEPSEEK_API_KEY，无法执行多 Agent 审阅。")
        schema = result_type.model_json_schema()
        body = {
            "model": self.settings.deepseek_model,
            "store": False,
            "instructions": instructions,
            "input": json.dumps(content, ensure_ascii=False),
            "reasoning": {"effort": "low"},
            "max_output_tokens": 4096,
            "text": {"format": {"type": "json_schema", "name": name, "schema": schema}},
        }
        try:
            response = httpx.post(
                f"{self.settings.deepseek_base_url.rstrip('/')}/responses",
                headers={"Authorization": f"Bearer {self.settings.deepseek_api_key}"},
                json=body,
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise LlmError(f"DeepSeek 调用失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise LlmError("模型返回格式无效：响应不是 JSON 对象。")
        output = _output_text(payload)
        if payload.get("status") != "completed" or not output:
            error = payload.get("error")
            incomplete = payload.get("incomplete_details")
            detail = (
                error.get("message") if isinstance(error, dict) else None
            ) or (
                incomplete.get("reason") if isinstance(incomplete, dict) else None
            ) or "未返回有效内容"
            raise LlmError(f"模型未完成有效响应：{detail}")
        try:
            return result_type.model_validate_json(output)
        except Exception as exc:  # Pydantic gives an actionable server-side job failure.
            raise LlmError("模型输出不符合预期结构。") from exc


def _output_text(payload: object) -> str:
    """Extract text from a Responses-compatible payload defensively.

    DeepSeek may include null placeholders alongside a message or within its
    content array. They are not output text and must not make an entire Agent
    run fail with ``'NoneType' object has no attribute 'get'``.
    """
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content", [])
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)
            )
    return "".join(parts)


llm_client = DeepSeekClient()
