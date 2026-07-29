from __future__ import annotations

from pathlib import Path


def sidecar_txt_backend(file_path: str, *, job=None, language: str = "") -> dict:
    """Development backend: reads '<media-file>.txt' and splits it into timed segments.

    Each non-empty line becomes one segment spaced 4 seconds apart, making it
    possible to exercise the full segments UI without a real Whisper install.

    Configure with:
        TRANSCRIPTION_BACKEND = "toto.transcription.backends.sidecar_txt_backend"
    """

    sidecar = Path(file_path).with_suffix(Path(file_path).suffix + ".txt")
    if not sidecar.exists():
        raise RuntimeError(f"Missing test transcript sidecar: {sidecar}")
    lines = [l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        lines = [sidecar.read_text(encoding="utf-8").strip() or "(empty)"]
    segment_ms = 4_000
    segments = [
        {"start_ms": i * segment_ms, "end_ms": (i + 1) * segment_ms, "text": line}
        for i, line in enumerate(lines)
    ]
    return {"language": language, "segments": segments}
