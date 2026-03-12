import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.vendor_asset_bridge import VendorAssetBridge


class _FakeR2:
    def get_bytes(self, key: str):
        return b"hello"


class _FakeS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


def test_vendor_asset_bridge_builds_public_url_and_key():
    bridge = VendorAssetBridge.__new__(VendorAssetBridge)
    bridge.bucket = "swiftcraft-vendor-public-assets"
    bridge.region = "us-east-2"
    bridge.prefix = "vendor-public"
    bridge.r2 = _FakeR2()
    bridge.s3 = _FakeS3()

    asset = asyncio.run(
        bridge.bridge_asset(
            source_key="uploads/demo/source.png",
            service="swap",
            asset_kind="source-face",
        )
    )

    assert asset.object_key.startswith("vendor-public/swap/source-face/")
    assert asset.public_url.startswith("https://swiftcraft-vendor-public-assets.s3.us-east-2.amazonaws.com/vendor-public/swap/source-face/")
    assert asset.content_type == "image/png"


def test_vendor_asset_bridge_accepts_local_source_path():
    bridge = VendorAssetBridge.__new__(VendorAssetBridge)
    bridge.bucket = "swiftcraft-vendor-public-assets"
    bridge.region = "us-east-2"
    bridge.prefix = "vendor-public"
    bridge.r2 = _FakeR2()
    bridge.s3 = _FakeS3()

    with TemporaryDirectory() as tmp_dir:
        local_path = Path(tmp_dir) / "target_face_01.jpg"
        local_path.write_bytes(b"jpg-bytes")
        asset = asyncio.run(
            bridge.bridge_asset(
                source_path=str(local_path),
                service="swap",
                asset_kind="target-face",
            )
        )

    assert asset.object_key.startswith("vendor-public/swap/target-face/")
    assert asset.content_type == "image/jpeg"
