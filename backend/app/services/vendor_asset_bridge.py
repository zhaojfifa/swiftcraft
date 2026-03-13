from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import boto3
import httpx

from app.core.config import settings
from app.services.r2_client import R2Client


class VendorAssetBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class BridgedVendorAsset:
    object_key: str
    public_url: str
    content_type: str
    sha256: str
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "storage_key": self.object_key,
            "cdn_url": self.public_url,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


class VendorAssetBridge:
    def __init__(self) -> None:
        if not settings.S3_VENDOR_BRIDGE_ENABLED:
            raise VendorAssetBridgeError("S3 vendor bridge is disabled")
        if not settings.S3_VENDOR_BRIDGE_BUCKET:
            raise VendorAssetBridgeError("Missing S3_VENDOR_BRIDGE_BUCKET")
        if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
            raise VendorAssetBridgeError("Missing AWS vendor bridge credentials")
        self.bucket = settings.S3_VENDOR_BRIDGE_BUCKET.strip()
        self.region = settings.S3_VENDOR_BRIDGE_REGION.strip() or "us-east-2"
        self.prefix = settings.S3_VENDOR_BRIDGE_PREFIX.strip().strip("/") or "vendor-public"
        self.r2 = R2Client()
        self.s3 = boto3.client(
            "s3",
            region_name=self.region,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

    def public_url(self, object_key: str) -> str:
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{object_key.lstrip('/')}"

    @staticmethod
    def _guess_ext(source_name: str | None, content_type: str | None) -> str:
        source = str(source_name or "").strip()
        ext = Path(urlparse(source).path).suffix.lower()
        if ext:
            return ext
        guessed = mimetypes.guess_extension(str(content_type or "").split(";")[0].strip() or "")
        return guessed or ".bin"

    @staticmethod
    def _guess_content_type(source_name: str | None, fallback: str | None = None) -> str:
        guessed, _ = mimetypes.guess_type(str(source_name or "").strip())
        return guessed or fallback or "application/octet-stream"

    async def _read_from_url(self, source_url: str) -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True) as client:
            response = await client.get(source_url)
            response.raise_for_status()
            return response.content, str(response.headers.get("content-type") or "").strip()

    async def _load_source(self, *, source_key: str | None, source_url: str | None, source_path: str | None) -> tuple[bytes, str, str]:
        if source_path:
            path = Path(source_path)
            if not path.exists():
                raise VendorAssetBridgeError(f"vendor bridge source path missing: {source_path}")
            body = path.read_bytes()
            content_type = self._guess_content_type(path.name)
            return body, content_type, path.name
        if source_key:
            body = self.r2.get_bytes(source_key)
            if body is None:
                raise VendorAssetBridgeError(f"vendor bridge source not found for key={source_key}")
            content_type = self._guess_content_type(source_key)
            return body, content_type, source_key
        if source_url:
            body, header_content_type = await self._read_from_url(source_url)
            content_type = self._guess_content_type(source_url, header_content_type or None)
            return body, content_type, source_url
        raise VendorAssetBridgeError("vendor bridge requires source_key or source_url")

    async def bridge_asset(
        self,
        *,
        source_key: str | None = None,
        source_url: str | None = None,
        source_path: str | None = None,
        service: str,
        asset_kind: str,
    ) -> BridgedVendorAsset:
        body, content_type, source_name = await self._load_source(
            source_key=source_key,
            source_url=source_url,
            source_path=source_path,
        )
        sha256 = hashlib.sha256(body).hexdigest()
        ext = self._guess_ext(source_name, content_type)
        object_key = f"{self.prefix}/{service.strip().lower()}/{asset_kind.strip().lower()}/{sha256}{ext}"
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=body,
                ContentType=content_type,
                CacheControl="public,max-age=604800",
            )
        except Exception as exc:
            raise VendorAssetBridgeError(f"vendor bridge upload failed for key={object_key}: {type(exc).__name__}: {exc}") from exc
        return BridgedVendorAsset(
            object_key=object_key,
            public_url=self.public_url(object_key),
            content_type=content_type,
            sha256=sha256,
            size_bytes=len(body),
        )
