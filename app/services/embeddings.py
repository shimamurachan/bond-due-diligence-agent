from __future__ import annotations

from google import genai
from google.genai import types

from app.config import Settings, get_settings


class EmbeddingError(RuntimeError):
    pass


class VertexAIEmbeddingClient:
    """Vertex AI embeddings for the pgvector-backed retrieval index.

    Application Default Credentials are used deliberately: Cloud Run obtains
    them from its runtime service account and local Docker receives them from
    the user's mounted Google Cloud CLI application-default credentials.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def model_name(self) -> str:
        return self.settings.vertex_ai_embedding_model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, query: str) -> list[float]:
        vectors = self._embed([query], task_type="RETRIEVAL_QUERY")
        return vectors[0]

    def _embed(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        if not texts:
            return []
        try:
            client = genai.Client(
                vertexai=True,
                project=self.settings.google_cloud_project,
                location=self.settings.vertex_ai_location,
                http_options=types.HttpOptions(api_version="v1"),
            )
            response = client.models.embed_content(
                model=self.model_name,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=self.settings.embedding_dimension,
                ),
            )
            vectors = [item.values for item in response.embeddings or []]
        except Exception as exc:
            raise EmbeddingError(
                "Vertex AI 向量服务调用失败。请确认已启用 Vertex AI API，运行身份拥有 "
                "Vertex AI User 权限；本地 Docker 还需要执行 gcloud auth application-default login。"
            ) from exc
        dimension = self.settings.embedding_dimension
        if len(vectors) != len(texts) or any(not isinstance(vector, list) or len(vector) != dimension for vector in vectors):
            raise EmbeddingError("Vertex AI 返回的向量数量或维度不正确。")
        return vectors


embedding_client = VertexAIEmbeddingClient()
