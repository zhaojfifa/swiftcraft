from app.engines.akool_swap_face_engine import AkoolSwapFaceEngine


class _FakeR2:
    def public_url(self, key: str) -> str:
        return f"https://cdn.example/{key.lstrip('/')}"


def test_resolve_public_url_keeps_http_url():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.r2 = _FakeR2()
    assert engine.resolve_public_url("https://cdn.example/uploads/source.mp4") == "https://cdn.example/uploads/source.mp4"


def test_resolve_public_url_converts_object_key():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.r2 = _FakeR2()
    assert engine.resolve_public_url("uploads/source.mp4") == "https://cdn.example/uploads/source.mp4"
