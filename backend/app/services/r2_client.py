from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

import boto3


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
        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.cfg.endpoint,
            aws_access_key_id=self.cfg.access_key_id,
            aws_secret_access_key=self.cfg.secret_access_key,
            region_name="auto",
        )

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
