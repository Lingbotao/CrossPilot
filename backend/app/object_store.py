"""商品图片对象存储。没有配置访问密钥时落在进程内，方便本地演示。"""

from __future__ import annotations

from typing import Protocol

import boto3

from app.core.config import settings


class ObjectStore(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> str: ...


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, body: bytes, content_type: str) -> str:
        del content_type
        self.objects[key] = body
        return key


class S3ObjectStore:
    def put(self, key: str, body: bytes, content_type: str) -> str:
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            use_ssl=settings.s3_use_ssl,
        )
        client.put_object(
            Bucket=settings.s3_bucket_product_image,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        return key


_MEMORY = MemoryObjectStore()


def default_object_store() -> ObjectStore:
    if settings.s3_access_key:
        return S3ObjectStore()
    return _MEMORY


__all__ = ["MemoryObjectStore", "ObjectStore", "S3ObjectStore", "default_object_store"]
