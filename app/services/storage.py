from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from google.cloud import storage

from app.config import Settings, get_settings


@dataclass(frozen=True)
class StoredObject:
    key: str
    content_type: str


class ObjectStore:
    """Private document store. Cloud Run uses GCS; Docker development uses a mounted folder."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: storage.Client | None = None

    @property
    def uses_gcs(self) -> bool:
        return self.settings.is_cloud and bool(self.settings.gcs_bucket)

    def _local_path(self, key: str) -> Path:
        target = (self.settings.local_storage_dir / key).resolve()
        root = self.settings.local_storage_dir.resolve()
        if root not in target.parents and target != root:
            raise ValueError("无效的存储路径。")
        return target

    def _bucket(self):
        if self._client is None:
            self._client = storage.Client(project=self.settings.google_cloud_project)
        return self._client.bucket(self.settings.gcs_bucket)

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        if self.uses_gcs:
            blob = self._bucket().blob(key)
            blob.upload_from_string(data, content_type=content_type)
        else:
            path = self._local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return StoredObject(key=key, content_type=content_type)

    def read(self, key: str) -> bytes:
        if self.uses_gcs:
            return self._bucket().blob(key).download_as_bytes()
        return self._local_path(key).read_bytes()

    def delete(self, key: str) -> None:
        if self.uses_gcs:
            self._bucket().blob(key).delete(if_generation_match=None)
            return
        path = self._local_path(key)
        if path.exists():
            path.unlink()


object_store = ObjectStore()
