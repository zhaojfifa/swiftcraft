from __future__ import annotations

import os
import re
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

_RUNTIME_LOGGED = False
_LAST_TRANSCRIBE_STATUS: dict[str, str] = {"status": "init", "reason": ""}
_RUNTIME_INSTALL_ATTEMPTED = False
_MODEL_LOCK = threading.Lock()
_MODEL_INSTANCE: Any = None
_MODEL_META: dict[str, str] = {"status": "cold", "model": "", "model_path": ""}
_SEMAPHORE_LOCK = threading.Lock()
_TRANSCRIBE_SEMAPHORE: threading.Semaphore | None = None
_TRANSCRIBE_SEMAPHORE_SIZE = 0
_HEARTBEAT_EXECUTOR = ThreadPoolExecutor(max_workers=1)
Logger = Optional[Callable[[str], None]]


@dataclass
class ASRSegment:
    start: float
    end: float
    text: str


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _env_float(name: str, default: Optional[float] = None) -> Optional[float]:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _env_first(names: list[str], default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return default


def _log(msg: str, logger: Logger = None) -> None:
    if logger:
        logger(msg)
    else:
        print(msg)


def get_last_transcribe_status() -> dict[str, str]:
    return dict(_LAST_TRANSCRIBE_STATUS)


def reset_last_transcribe_status() -> None:
    global _LAST_TRANSCRIBE_STATUS
    _LAST_TRANSCRIBE_STATUS = {"status": "init", "reason": ""}


def _env_runtime_install_enabled() -> bool:
    value = os.getenv("ASR_RUNTIME_INSTALL_ON_MISSING")
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_model_name(model_name: str | None = None) -> str:
    return (model_name or _env_first(["ASR_MODEL", "FASTWHISPER_MODEL"], "tiny")).strip() or "tiny"


def _resolve_compute_type() -> str:
    return os.getenv("ASR_COMPUTE_TYPE", os.getenv("FASTWHISPER_COMPUTE_TYPE", "int8")).strip() or "int8"


def _resolve_device() -> str:
    return os.getenv("FASTWHISPER_DEVICE", "cpu").strip() or "cpu"


def _transcribe_timeout_sec() -> int:
    return max(30, _env_int("ASR_TRANSCRIBE_TIMEOUT_SEC", 120))


def _heartbeat_interval_sec() -> int:
    return max(5, _env_int("ASR_HEARTBEAT_SEC", 10))


def _use_subprocess_asr() -> bool:
    return _env_bool("ASR_USE_SUBPROCESS", True)


def _get_semaphore() -> threading.Semaphore:
    global _TRANSCRIBE_SEMAPHORE
    global _TRANSCRIBE_SEMAPHORE_SIZE
    size = max(1, _env_int("ASR_MAX_CONCURRENCY", 1))
    with _SEMAPHORE_LOCK:
        if _TRANSCRIBE_SEMAPHORE is None or _TRANSCRIBE_SEMAPHORE_SIZE != size:
            _TRANSCRIBE_SEMAPHORE = threading.Semaphore(size)
            _TRANSCRIBE_SEMAPHORE_SIZE = size
    return _TRANSCRIBE_SEMAPHORE


def _cache_root() -> str:
    root = (os.getenv("ASR_HF_CACHE_DIR", "") or os.getenv("HF_HOME", "") or "").strip()
    if root:
        root_path = Path(root)
    else:
        root_path = Path.home() / ".cache" / "huggingface"
    (root_path / "hub").mkdir(parents=True, exist_ok=True)
    return str(root_path)


def _model_local_dir(model_name: str) -> Path:
    base = Path((os.getenv("ASR_MODEL_DIR", "/var/data/asr_models") or "/var/data/asr_models")).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"faster-whisper-{model_name}"


def _local_dir_ready(local_dir: Path) -> bool:
    required = ("model.bin", "config.json", "tokenizer.json")
    return any((local_dir / name).exists() for name in required)


def _log_dir_tree(local_dir: Path, logger: Logger = None) -> None:
    if not local_dir.exists():
        _log(f"local_dir_tree missing path={local_dir}", logger)
        return
    top_entries = sorted(local_dir.iterdir(), key=lambda p: p.name)[:20]
    _log(f"local_dir_tree path={local_dir} entries={len(top_entries)}", logger)
    for entry in top_entries:
        if entry.is_dir():
            nested = sorted(entry.iterdir(), key=lambda p: p.name)[:20]
            _log(f"local_dir_tree dir={entry.name} nested={len(nested)}", logger)
            for child in nested:
                _log(f"local_dir_tree - {entry.name}/{child.name}", logger)
        else:
            _log(f"local_dir_tree - {entry.name}", logger)


def _env_offline() -> bool:
    value = (os.getenv("HF_HUB_OFFLINE", "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _ensure_local_model_dir(model_name: str, logger: Logger = None) -> str:
    local_dir = _model_local_dir(model_name)
    _log(f"ensure_local_model_dir start model={model_name} local_dir={local_dir} rss_mb={_rss_mb()}", logger)
    if _local_dir_ready(local_dir):
        entries = len(list(local_dir.iterdir())) if local_dir.exists() else 0
        _log(f"ensure_local_model_dir ready model={model_name} entries={entries} rss_mb={_rss_mb()}", logger)
        return str(local_dir)
    if _env_offline():
        raise RuntimeError(f"offline_local_dir_missing:{local_dir}")
    started = time.perf_counter()
    _log(
        f"snapshot_download start model={model_name} local_dir={local_dir} cache_dir={_cache_root()} rss_mb={_rss_mb()}",
        logger,
    )
    from huggingface_hub import snapshot_download  # type: ignore

    snapshot_download(
        repo_id=f"Systran/faster-whisper-{model_name}",
        local_dir=str(local_dir),
        local_dir_use_symlinks=_env_bool("ASR_MODEL_LOCALDIR_USE_SYMLINKS", False),
        cache_dir=(os.getenv("ASR_HF_CACHE_DIR") or None),
        resume_download=True,
        allow_patterns=["*"],
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    entries = len(list(local_dir.iterdir())) if local_dir.exists() else 0
    _log(f"snapshot_download done local_dir={local_dir} elapsed_ms={elapsed_ms} entries={entries}", logger)
    if not _local_dir_ready(local_dir):
        _log_dir_tree(local_dir, logger)
        raise RuntimeError(f"local_dir_not_ready_after_download:{local_dir}")
    return str(local_dir)


def _resolve_model_path_or_name(model_name: str, logger: Logger = None) -> str:
    return _ensure_local_model_dir(model_name, logger=logger)


def _rss_mb() -> int:
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception:
        return -1


def _asr_runtime_modules() -> dict[str, Any]:
    modules: dict[str, Any] = {}
    for name in ("faster_whisper", "ctranslate2", "tokenizers", "huggingface_hub", "requests", "onnxruntime", "av"):
        try:
            mod = __import__(name)
            modules[name] = {
                "ok": True,
                "version": getattr(mod, "__version__", "unknown"),
            }
        except Exception as exc:
            modules[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return modules


def _try_runtime_install(logger: Logger = None) -> bool:
    global _RUNTIME_INSTALL_ATTEMPTED
    if _RUNTIME_INSTALL_ATTEMPTED:
        return False
    _RUNTIME_INSTALL_ATTEMPTED = True
    if not _env_runtime_install_enabled():
        return False
    _log("faster_whisper missing -> attempting runtime install", logger)
    commands = [
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "ctranslate2>=4.3.0",
            "tokenizers",
            "huggingface-hub",
            "tqdm",
            "numpy",
        ],
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--no-deps",
            "faster-whisper==1.0.3",
        ],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        except Exception as exc:
            _log(f"runtime install failed cmd={' '.join(cmd)} err={type(exc).__name__}: {exc}", logger)
            return False
    return True


def get_asr_runtime_info() -> dict[str, Any]:
    model_name = _resolve_model_name(None)
    compute_type = _resolve_compute_type()
    device = _resolve_device()
    local_dir = _model_local_dir(model_name)
    try:
        resolved_model_input = str(local_dir)
    except Exception as exc:
        resolved_model_input = f"unresolved:{type(exc).__name__}:{exc}"
    with _MODEL_LOCK:
        model_meta = dict(_MODEL_META)
        loaded = _MODEL_INSTANCE is not None
    return {
        "model": model_name,
        "compute_type": compute_type,
        "device": device,
        "max_concurrency": max(1, _env_int("ASR_MAX_CONCURRENCY", 1)),
        "timeout_sec": _transcribe_timeout_sec(),
        "heartbeat_sec": _heartbeat_interval_sec(),
        "hf_home": _cache_root(),
        "model_cache_path": str(local_dir),
        "model_cache_ready": _local_dir_ready(local_dir),
        "resolved_model_input": resolved_model_input,
        "runtime_modules": _asr_runtime_modules(),
        "model_loaded": loaded,
        "model_meta": model_meta,
    }


def _load_model_once(model_name: str, logger: Logger = None) -> Any:
    from faster_whisper import WhisperModel  # type: ignore

    device = _resolve_device()
    compute_type = _resolve_compute_type()
    cpu_threads = max(1, _env_int("ASR_CPU_THREADS", 1))
    num_workers = max(1, _env_int("ASR_NUM_WORKERS", 1))
    model_input = _resolve_model_path_or_name(model_name, logger=logger)
    _log(
        f"model_load start model={model_name} compute_type={compute_type} device={device} "
        f"cpu_threads={cpu_threads} num_workers={num_workers} rss_mb={_rss_mb()} "
        f"pid={os.getpid()} tid={threading.get_ident()}",
        logger,
    )
    started = time.perf_counter()
    model = WhisperModel(
        model_input,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
        num_workers=num_workers,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    model_path = str(
        getattr(model, "model_size_or_path", None)
        or getattr(getattr(model, "model", None), "model_path", "")
        or ""
    )
    _log(
        f"model_load ok elapsed_ms={elapsed_ms} model_path={model_path} rss_mb={_rss_mb()} "
        f"pid={os.getpid()} tid={threading.get_ident()}",
        logger,
    )
    with _MODEL_LOCK:
        _MODEL_META.update(
            {
                "status": "warm",
                "model": model_name,
                "model_path": model_path,
                "resolved_model_input": str(model_input),
            }
        )
    return model


def get_whisper_model(model_name: str | None = None, logger: Logger = None) -> Any:
    global _MODEL_INSTANCE
    chosen_model = _resolve_model_name(model_name)
    with _MODEL_LOCK:
        if _MODEL_INSTANCE is not None and _MODEL_META.get("model") == chosen_model:
            return _MODEL_INSTANCE
    try:
        from faster_whisper import WhisperModel  # noqa: F401  # type: ignore
    except ModuleNotFoundError:
        if not _try_runtime_install(logger=logger):
            raise
        from faster_whisper import WhisperModel  # noqa: F401  # type: ignore
    with _MODEL_LOCK:
        if _MODEL_INSTANCE is not None and _MODEL_META.get("model") == chosen_model:
            return _MODEL_INSTANCE
        _MODEL_INSTANCE = _load_model_once(chosen_model, logger=logger)
        return _MODEL_INSTANCE


def warmup_asr_model(model_name: str | None = None) -> dict[str, str]:
    chosen_model = _resolve_model_name(model_name)
    model_path = str(_model_local_dir(chosen_model))
    status = "ok" if _local_dir_ready(Path(model_path)) else "missing_cache"
    return {
        "status": status,
        "model": chosen_model,
        "model_path": model_path,
    }


def _run_with_heartbeat(
    func: Any,
    *,
    phase: str,
    timeout_sec: int,
    heartbeat_sec: int,
    logger: Logger = None,
) -> Any:
    started = time.perf_counter()
    future = _HEARTBEAT_EXECUTOR.submit(func)
    try:
        while True:
            try:
                return future.result(timeout=heartbeat_sec)
            except FutureTimeoutError:
                elapsed = int(time.perf_counter() - started)
                _log(f"heartbeat phase={phase} elapsed_s={elapsed}", logger)
                if elapsed >= timeout_sec:
                    future.cancel()
                    raise TimeoutError(f"{phase} timeout after {timeout_sec}s")
    finally:
        pass


def _run_subprocess_transcribe(
    *,
    wav_path: str,
    model_input: str,
    language: str | None,
    beam_size: int,
    vad_filter: bool,
    word_timestamps: bool,
    vad_min_silence_ms: int,
    vad_speech_pad_ms: int,
    no_speech_threshold: float | None,
    device: str,
    compute_type: str,
    cpu_threads: int,
    num_workers: int,
    timeout_sec: int,
    logger: Logger = None,
) -> tuple[list[ASRSegment], str]:
    payload: dict[str, Any] = {
        "wav_path": wav_path,
        "model_input": model_input,
        "language": language,
        "beam_size": beam_size,
        "vad_filter": vad_filter,
        "word_timestamps": word_timestamps,
        "vad_min_silence_ms": vad_min_silence_ms,
        "vad_speech_pad_ms": vad_speech_pad_ms,
        "no_speech_threshold": no_speech_threshold,
        "device": device,
        "compute_type": compute_type,
        "cpu_threads": cpu_threads,
        "num_workers": num_workers,
    }
    cmd = [sys.executable, "-m", "app.utils.asr_worker"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _log(f"subprocess_start pid={proc.pid}", logger)
    started = time.perf_counter()
    try:
        stdout, stderr = proc.communicate(json.dumps(payload), timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        proc.kill()
        _, stderr = proc.communicate()
        _log(f"subprocess_timeout pid={proc.pid} timeout_sec={timeout_sec} elapsed_ms={elapsed_ms}", logger)
        if stderr:
            _log(f"subprocess_stderr_tail={stderr[-1200:]}", logger)
        raise TimeoutError("transcribe_timeout")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _log(f"subprocess_done rc={proc.returncode} elapsed_ms={elapsed_ms}", logger)
    if stderr:
        _log(f"subprocess_stderr_tail={stderr[-1200:]}", logger)
    if proc.returncode != 0:
        raise RuntimeError(f"subprocess_failed rc={proc.returncode}")
    try:
        data = json.loads(stdout or "{}")
    except Exception as exc:
        raise RuntimeError(f"subprocess_output_invalid:{exc}") from exc
    status = str(data.get("status") or "error")
    if status != "ok":
        raise RuntimeError(f"subprocess_status:{status}:{data.get('reason')}")
    raw_segments = data.get("segments") or []
    segs = _to_segments(raw_segments)
    return segs, status


def _probe_duration_sec(audio_wav_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_wav_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return max(1.0, float((result.stdout or "1").strip()))
    except Exception:
        return 1.0


def _to_segments(raw_segments: Any) -> List[ASRSegment]:
    segments: List[ASRSegment] = []
    for seg in raw_segments:
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start + 1.0) or (start + 1.0))
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        segments.append(ASRSegment(start=start, end=max(end, start + 0.1), text=text))
    return segments


def _split_text_sentences(text: str) -> List[str]:
    parts: List[str] = []
    for piece in re.split(r"[\n]+", text):
        stripped = piece.strip()
        if not stripped:
            continue
        chunks = re.split(r"(?<=[\u3002\uff01\uff1f!?.,;\uff0c\uff1b])\s*", stripped)
        for chunk in chunks:
            chunk_text = chunk.strip()
            if chunk_text:
                parts.append(chunk_text)
    return parts


def _chunk_text_by_chars(text: str, max_chars: int = 22) -> List[str]:
    clean = " ".join(text.split()).strip()
    if not clean:
        return []
    chunks: List[str] = []
    cursor = 0
    while cursor < len(clean):
        end = min(len(clean), cursor + max_chars)
        if end < len(clean):
            space_idx = clean.rfind(" ", cursor, end)
            if space_idx > cursor:
                end = space_idx
        chunk = clean[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        cursor = end if end > cursor else cursor + max_chars
    return chunks


def _allocate_timings(start: float, end: float, pieces: List[str]) -> List[ASRSegment]:
    if not pieces:
        return [ASRSegment(start=start, end=max(end, start + 0.12), text="...")]
    total_span = max(0.12, end - start)
    weights = [max(1, len(re.sub(r"\s+", "", item))) for item in pieces]
    total_weight = max(1, sum(weights))

    starts: List[float] = []
    ends: List[float] = []
    cursor = start
    for idx, weight in enumerate(weights):
        seg_start = cursor
        if idx == len(weights) - 1:
            seg_end = end
        else:
            seg_span = total_span * (weight / total_weight)
            seg_end = seg_start + max(0.12, seg_span)
        starts.append(seg_start)
        ends.append(seg_end)
        cursor = seg_end

    min_dur = 0.12
    for idx in range(len(starts)):
        if idx > 0:
            starts[idx] = max(starts[idx], ends[idx - 1])
        ends[idx] = max(ends[idx], starts[idx] + min_dur)
    ends[-1] = max(end, starts[-1] + min_dur)
    for idx in range(len(starts) - 2, -1, -1):
        starts[idx] = min(starts[idx], starts[idx + 1] - min_dur)
        ends[idx] = min(max(ends[idx], starts[idx] + min_dur), starts[idx + 1])

    return [ASRSegment(start=max(0.0, starts[i]), end=max(starts[i] + min_dur, ends[i]), text=pieces[i]) for i in range(len(pieces))]


def _split_single_segment(seg: ASRSegment, total_duration: float) -> List[ASRSegment]:
    seg_start = max(0.0, seg.start)
    seg_end = max(seg_start + 0.12, min(seg.end, max(total_duration, seg.start + 0.12)))
    pieces = _split_text_sentences(seg.text)
    if len(pieces) <= 1:
        pieces = _chunk_text_by_chars(seg.text, max_chars=22)
    if len(pieces) <= 1:
        span = max(0.12, seg_end - seg_start)
        piece_count = max(2, min(6, int(round(span / 1.2))))
        text = " ".join(seg.text.split()).strip() or "Localized narration."
        if len(text) < piece_count * 4:
            pieces = [text for _ in range(piece_count)]
        else:
            pieces = _chunk_text_by_chars(text, max(12, int(len(text) / piece_count) + 1))
    return _allocate_timings(seg_start, seg_end, pieces)


def _empty_fallback_segments(duration: float) -> List[ASRSegment]:
    total_duration = max(1.0, duration)
    cue_count = 2 if total_duration < 6.0 else (3 if total_duration < 12.0 else 4)
    pieces = ["Localized narration." for _ in range(cue_count)]
    return _allocate_timings(0.0, total_duration, pieces)


def transcribe(
    audio_wav_path: str,
    *,
    model_name: str | None = None,
    beam_size: int | None = None,
    vad_filter: bool | None = None,
    language: str | None = None,
    no_speech_threshold: float | None = None,
    logger: Logger = None,
) -> List[ASRSegment]:
    global _RUNTIME_LOGGED
    global _LAST_TRANSCRIBE_STATUS
    _LAST_TRANSCRIBE_STATUS = {"status": "start", "reason": ""}
    model_name = _resolve_model_name(model_name)
    device = _resolve_device()
    compute_type = _resolve_compute_type()
    if beam_size is None:
        beam_size = _env_int("ASR_BEAM_SIZE", _env_int("FASTWHISPER_BEAM_SIZE", 1))
    if vad_filter is None:
        vad_filter = _env_bool("ASR_VAD_FILTER", _env_bool("FASTWHISPER_VAD_FILTER", True))
    vad_min_silence_ms = _env_int("FASTWHISPER_VAD_MIN_SILENCE_MS", 250)
    vad_speech_pad_ms = _env_int("FASTWHISPER_VAD_SPEECH_PAD_MS", 150)
    word_timestamps = _env_bool("ASR_WORD_TIMESTAMPS", _env_bool("FASTWHISPER_WORD_TIMESTAMPS", False))
    if language is None:
        language = _env_first(["ASR_LANGUAGE_HINT", "FASTWHISPER_LANGUAGE"], "") or None
    if no_speech_threshold is None:
        no_speech_threshold = _env_float("ASR_NO_SPEECH_THRESHOLD")
    timeout_sec = _transcribe_timeout_sec()
    heartbeat_sec = _heartbeat_interval_sec()
    model_load_in_thread = _env_bool("ASR_MODEL_LOAD_IN_THREAD", False)
    use_subprocess = _use_subprocess_asr()
    cpu_threads = max(1, _env_int("ASR_CPU_THREADS", 1))
    num_workers = max(1, _env_int("ASR_NUM_WORKERS", 1))

    if not _RUNTIME_LOGGED:
        try:
            import sys

            _log(
                f"python={sys.executable} ver={sys.version.split()[0]} "
                f"model={model_name} device={device} compute={compute_type} "
                f"vad={vad_filter} lang={language} beam={beam_size} "
                f"no_speech_threshold={no_speech_threshold}",
                logger,
            )
        except Exception:
            pass
        _RUNTIME_LOGGED = True

    semaphore = _get_semaphore()
    acquired = semaphore.acquire(timeout=max(timeout_sec, 30))
    if not acquired:
        _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": "semaphore_timeout"}
        duration = _probe_duration_sec(audio_wav_path)
        return _empty_fallback_segments(duration)

    try:
        model_input = ""
        model = None
        try:
            _log(f"rss_mb={_rss_mb()} phase=before_model_load", logger)
            model_input = _ensure_local_model_dir(model_name, logger=logger)
            if use_subprocess:
                _log(f"model_ready model={model_name} local_path={model_input} rss_mb={_rss_mb()}", logger)
            elif model_load_in_thread:
                model = _run_with_heartbeat(
                    lambda: get_whisper_model(model_name, logger=logger),
                    phase="model_load",
                    timeout_sec=timeout_sec,
                    heartbeat_sec=heartbeat_sec,
                    logger=logger,
                )
            else:
                model = get_whisper_model(model_name, logger=logger)
            _log(f"rss_mb={_rss_mb()} phase=after_model_load", logger)
            if not use_subprocess:
                _log(f"model_ready model={model_name} rss_mb={_rss_mb()}", logger)
        except ModuleNotFoundError as exc:
            missing_module = getattr(exc, "name", "unknown")
            _log(f"faster_whisper_not_installed -> fallback missing_module={missing_module}", logger)
            _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": f"module_not_found:{missing_module}"}
            duration = _probe_duration_sec(audio_wav_path)
            return _empty_fallback_segments(duration)
        except RuntimeError as exc:
            _log(f"model_resolve_failed -> fallback reason={exc}", logger)
            _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": f"model_resolve_failed:{exc}"}
            duration = _probe_duration_sec(audio_wav_path)
            return _empty_fallback_segments(duration)
        except TimeoutError:
            _log("model_load timeout -> fallback", logger)
            _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": "timeout_model_load"}
            duration = _probe_duration_sec(audio_wav_path)
            return _empty_fallback_segments(duration)

        full_kwargs: dict[str, Any] = {
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "word_timestamps": word_timestamps,
            "vad_parameters": {
                "min_silence_duration_ms": vad_min_silence_ms,
                "speech_pad_ms": vad_speech_pad_ms,
            },
        }
        if language:
            full_kwargs["language"] = language
        if no_speech_threshold is not None:
            full_kwargs["no_speech_threshold"] = no_speech_threshold
        _log(f"rss_mb={_rss_mb()} phase=before_transcribe", logger)
        _log(
            f"transcribe_start lang={language or 'auto'} beam={beam_size} vad={vad_filter} "
            f"word_ts={word_timestamps} wav={audio_wav_path} rss_mb={_rss_mb()}",
            logger,
        )
        transcribe_started = time.perf_counter()

        def _do_transcribe() -> Any:
            try:
                return model.transcribe(audio_wav_path, **full_kwargs)
            except TypeError:
                fallback_kwargs: dict[str, Any] = {
                    "beam_size": beam_size,
                    "vad_filter": vad_filter,
                }
                if language:
                    fallback_kwargs["language"] = language
                return model.transcribe(audio_wav_path, **fallback_kwargs)

        try:
            if use_subprocess:
                preview_segments, _ = _run_subprocess_transcribe(
                    wav_path=audio_wav_path,
                    model_input=model_input,
                    language=language,
                    beam_size=beam_size,
                    vad_filter=vad_filter,
                    word_timestamps=word_timestamps,
                    vad_min_silence_ms=vad_min_silence_ms,
                    vad_speech_pad_ms=vad_speech_pad_ms,
                    no_speech_threshold=no_speech_threshold,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=cpu_threads,
                    num_workers=num_workers,
                    timeout_sec=timeout_sec,
                    logger=logger,
                )
                raw_segments = preview_segments
            else:
                raw_segments, _ = _run_with_heartbeat(
                    _do_transcribe,
                    phase="transcribe",
                    timeout_sec=timeout_sec,
                    heartbeat_sec=heartbeat_sec,
                    logger=logger,
                )
        except TimeoutError:
            elapsed_ms = int((time.perf_counter() - transcribe_started) * 1000)
            _log(f"transcribe_timeout timeout_sec={timeout_sec} elapsed_ms={elapsed_ms} rss_mb={_rss_mb()}", logger)
            _LAST_TRANSCRIBE_STATUS = {"status": "timeout", "reason": "transcribe_timeout"}
            duration = _probe_duration_sec(audio_wav_path)
            return _empty_fallback_segments(duration)

        transcribe_elapsed_ms = int((time.perf_counter() - transcribe_started) * 1000)
        preview_segments = raw_segments if use_subprocess else _to_segments(raw_segments)
        preview_text = " ".join(seg.text for seg in preview_segments).strip()
        _log(f"rss_mb={_rss_mb()} phase=after_transcribe", logger)
        _log(
            f"transcribe_done elapsed_ms={transcribe_elapsed_ms} "
            f"segments={len(preview_segments)} text_len={len(preview_text)} rss_mb={_rss_mb()}",
            logger,
        )
    except Exception as exc:
        _log(f"exception={type(exc).__name__}: {exc} -> fallback", logger)
        _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": f"runtime_exception:{type(exc).__name__}"}
        duration = _probe_duration_sec(audio_wav_path)
        return _empty_fallback_segments(duration)
    finally:
        semaphore.release()

    segments = raw_segments if use_subprocess else _to_segments(raw_segments)
    total_duration = _probe_duration_sec(audio_wav_path)
    if len(segments) == 1 and (segments[0].end - segments[0].start) >= 2.5:
        _LAST_TRANSCRIBE_STATUS = {"status": "ok", "reason": "single_segment_split"}
        return _split_single_segment(segments[0], total_duration=total_duration)
    if segments:
        _LAST_TRANSCRIBE_STATUS = {"status": "ok", "reason": "segments"}
        return segments
    _log("empty_segments -> fallback", logger)
    _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": "empty_segments"}
    return _empty_fallback_segments(total_duration)


def _srt_time(sec: float) -> str:
    total_ms = max(0, int(round(sec * 1000)))
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def segments_to_srt(segments: List[ASRSegment]) -> str:
    lines: List[str] = []
    for idx, seg in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(f"{_srt_time(seg.start)} --> {_srt_time(seg.end)}")
        lines.append(seg.text.strip() or "...")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
