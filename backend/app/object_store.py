"""对象存储。没有配置访问密钥时，商品图片落在进程内，方便本地演示。

导出文件优先写导出桶。桶连不上时改写本机临时目录，下载仍走接口，不发公网地址。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Protocol

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class ObjectStore(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> str: ...

    def get(self, key: str) -> bytes | None: ...

    def delete(self, key: str) -> None: ...


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, body: bytes, content_type: str) -> str:
        del content_type
        self.objects[key] = body
        return key

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class DirectoryObjectStore:
    """本机目录。键里不允许绝对路径或上级目录。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, key: str, body: bytes, content_type: str) -> str:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return key

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        relative = Path(key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("对象键不合法")
        return self.root / relative


class S3ObjectStore:
    def __init__(self, bucket: str | None = None, *, connect_timeout: float | None = None) -> None:
        self.bucket = bucket or settings.s3_bucket_product_image
        self.connect_timeout = connect_timeout

    def put(self, key: str, body: bytes, content_type: str) -> str:
        self._client().put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes | None:
        try:
            response = self._client().get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        body = response["Body"].read()
        if not isinstance(body, bytes):
            raise TypeError("对象存储返回的不是字节")
        return body

    def delete(self, key: str) -> None:
        self._client().delete_object(Bucket=self.bucket, Key=key)

    def _client(self) -> Any:
        extra: dict[str, Any] = {}
        if self.connect_timeout is not None:
            from botocore.config import Config

            extra["config"] = Config(
                connect_timeout=self.connect_timeout,
                read_timeout=self.connect_timeout,
                retries={"max_attempts": 1},
            )
        return boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            use_ssl=settings.s3_use_ssl,
            **extra,
        )


class ResilientExportStore:
    """同一进程里记住对象存储是否已经退回本机目录。"""

    def __init__(self) -> None:
        self._local = DirectoryObjectStore(Path(tempfile.gettempdir()) / "crosspilot-export")
        self._remote: S3ObjectStore | None = None
        if settings.s3_access_key:
            self._remote = S3ObjectStore(settings.s3_bucket_export, connect_timeout=2)

    def put(self, key: str, body: bytes, content_type: str) -> str:
        remote = self._remote
        if remote is None:
            return self._local.put(key, body, content_type)
        try:
            return remote.put(key, body, content_type)
        except Exception:
            log.warning("export_store_local_fallback", key=key)
            self._remote = None
            return self._local.put(key, body, content_type)

    def get(self, key: str) -> bytes | None:
        remote = self._remote
        if remote is not None:
            try:
                found = remote.get(key)
            except Exception:
                log.warning("export_store_local_fallback", key=key)
                self._remote = None
                found = None
            if found is not None:
                return found
        return self._local.get(key)

    def delete(self, key: str) -> None:
        remote = self._remote
        if remote is not None:
            try:
                remote.delete(key)
            except Exception:
                log.warning("export_store_local_fallback", key=key)
                self._remote = None
        self._local.delete(key)


_MEMORY = MemoryObjectStore()
_EXPORT = ResilientExportStore()


def default_object_store() -> ObjectStore:
    if settings.s3_access_key:
        return S3ObjectStore()
    return _MEMORY


def export_object_store() -> ObjectStore:
    """导出文件与商品图片分开。进程内共享同一个回退决定。"""

    return _EXPORT


__all__ = [
    "DirectoryObjectStore",
    "MemoryObjectStore",
    "ObjectStore",
    "ResilientExportStore",
    "S3ObjectStore",
    "default_object_store",
    "export_object_store",
]
