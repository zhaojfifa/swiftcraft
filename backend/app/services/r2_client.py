from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)


class R2ClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class R2Config:
    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    public_base: str


def _require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def load_r2_config() -> R2Config:
    return R2Config(
        endpoint=_require_env("R2_ENDPOINT"),
        bucket=_require_env("R2_BUCKET"),
        access_key_id=_require_env("R2_ACCESS_KEY_ID"),
        secret_access_key=_require_env("R2_SECRET_ACCESS_KEY"),
        public_base=_require_env("R2_PUBLIC_BASE").rstrip("/"),
    )


class R2Client:
    def __init__(self, cfg: Optional[R2Config] = None) -> None:
        self.cfg = cfg or load_r2_config()
        self._retry_delays = (0.0, 0.2)
        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.cfg.endpoint,
            aws_access_key_id=self.cfg.access_key_id,
            aws_secret_access_key=self.cfg.secret_access_key,
            region_name="auto",
            config=Config(
                connect_timeout=5,
                read_timeout=5,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )

    @staticmethod
    def _is_not_found(exc: ClientError) -> bool:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        return code in ("NoSuchKey", "404", "NotFound")

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (EndpointConnectionError, ConnectionClosedError, ReadTimeoutError, ConnectTimeoutError)):
            return True
        if isinstance(exc, ClientError):
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)
            return status in (429, 500, 502, 503, 504)
        return isinstance(exc, BotoCoreError)

    def public_url(self, key: str) -> str:
        key = key.lstrip("/")
        return f"{self.cfg.public_base}/{key}"

    def presign_put(self, key: str, content_type: str, expires_in: int = 900) -> Dict[str, object]:
        """
        Returns:
          - upload_url: presigned PUT url
          - headers: caller must send Content-Type exactly
        """
        key = key.lstrip("/")
        params = {
            "Bucket": self.cfg.bucket,
            "Key": key,
            "ContentType": content_type,
        }
        upload_url = self.s3.generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=expires_in,
        )
        return {
            "upload_url": upload_url,
            "headers": {"Content-Type": content_type},
            "expires_in": expires_in,
        }

    def copy_object(self, src_key: str, dst_key: str) -> None:
        src_key = src_key.lstrip("/")
        dst_key = dst_key.lstrip("/")
        copy_source = {"Bucket": self.cfg.bucket, "Key": src_key}
        self.s3.copy_object(
            Bucket=self.cfg.bucket,
            Key=dst_key,
            CopySource=copy_source,
        )

    def upload_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        key = key.lstrip("/")
        self.s3.put_object(
            Bucket=self.cfg.bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
        )
        return self.public_url(key)

    def put_json(self, key: str, data: Dict[str, object]) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.put_bytes(key, payload, content_type="application/json")

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        retries: int = 1,
    ) -> None:
        key = key.lstrip("/")
        last_exc: Exception | None = None
        retry_delays = self._retry_delays[: max(1, retries + 1)]
        for attempt, delay in enumerate(retry_delays):
            if delay:
                time.sleep(delay)
            try:
                self.s3.put_object(
                    Bucket=self.cfg.bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
                return
            except Exception as exc:
                last_exc = exc
                if attempt >= len(retry_delays) - 1 or not self._is_retryable(exc):
                    raise R2ClientError(f"put_bytes failed for key={key}: {type(exc).__name__}: {exc}") from exc
        if last_exc is not None:
            raise R2ClientError(f"put_bytes failed for key={key}: {type(last_exc).__name__}: {last_exc}") from last_exc

    def get_json(self, key: str) -> Optional[Dict[str, object]]:
        body = self.get_bytes(key)
        if body is None:
            return None
        return json.loads(body.decode("utf-8"))

    def get_bytes(self, key: str, timeout_sec: int = 2, retries: int = 1) -> Optional[bytes]:
        key = key.lstrip("/")
        last_exc: Exception | None = None
        retry_delays = self._retry_delays[: max(1, retries + 1)]
        started = time.time()
        for attempt, delay in enumerate(retry_delays):
            if delay:
                time.sleep(delay)
            try:
                response = self.s3.get_object(Bucket=self.cfg.bucket, Key=key)
                body = response["Body"].read()
                if not body:
                    return None
                return body
            except ClientError as exc:
                if self._is_not_found(exc):
                    return None
                last_exc = exc
                elapsed = time.time() - started
                if elapsed >= timeout_sec or attempt >= len(retry_delays) - 1 or not self._is_retryable(exc):
                    raise R2ClientError(f"get_bytes failed for key={key}: {type(exc).__name__}: {exc}") from exc
            except Exception as exc:
                last_exc = exc
                elapsed = time.time() - started
                if elapsed >= timeout_sec or attempt >= len(retry_delays) - 1 or not self._is_retryable(exc):
                    raise R2ClientError(f"get_bytes failed for key={key}: {type(exc).__name__}: {exc}") from exc
        if last_exc is not None:
            raise R2ClientError(f"get_bytes failed for key={key}: {type(last_exc).__name__}: {last_exc}") from last_exc
        return None

    def exists(self, key: str) -> bool:
        key = key.lstrip("/")
        try:
            self.s3.head_object(Bucket=self.cfg.bucket, Key=key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                return False
            raise

