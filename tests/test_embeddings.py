from types import SimpleNamespace

from app.config import Settings
from app.services import embeddings


def test_vertex_embedding_client_uses_retrieval_task_types(monkeypatch) -> None:
    captured: list[dict] = []

    class FakeModels:
        def embed_content(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.25] * 1024) for _ in kwargs["contents"]]
            )

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(embeddings.genai, "Client", lambda **_kwargs: FakeClient())
    client = embeddings.VertexAIEmbeddingClient(
        Settings(database_url="postgresql+psycopg://test:test@localhost/test")
    )

    assert client.embed_documents(["募集资金用于偿还借款"])[0] == [0.25] * 1024
    assert client.embed_query("募集资金用途") == [0.25] * 1024
    assert captured[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert captured[1]["config"].task_type == "RETRIEVAL_QUERY"
    assert captured[0]["config"].output_dimensionality == 1024
