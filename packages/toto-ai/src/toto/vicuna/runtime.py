"""Ollama runtime detection utilities — no hard external dependencies.

Settings:
    VICUNA_OLLAMA_HOST      default: http://localhost:11434
    VICUNA_REQUIRE_GPU      default: False
"""
from __future__ import annotations

import os
import shutil
import subprocess


def detect_gpu() -> dict:
    """Probe for GPU availability without importing torch or CUDA bindings.

    Checks (in order):
    1. ``nvidia-smi`` is on PATH and exits 0.
    2. ``/dev/nvidia0`` device node exists.
    3. ``NVIDIA_VISIBLE_DEVICES`` env var is set to a non-empty, non-"none" value.

    Returns a dict with:
        available (bool)   — True if a GPU was found by any method.
        backend  (str)     — "nvidia" | "none"
        details  (str)     — human-readable summary for logging/printing.

    Never raises.
    """
    try:
        if shutil.which("nvidia-smi"):
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                names = result.stdout.strip().replace("\n", ", ")
                return {
                    "available": True,
                    "backend": "nvidia",
                    "details": f"nvidia-smi: {names[:120]}",
                }
    except Exception:
        pass

    try:
        if os.path.exists("/dev/nvidia0"):
            return {
                "available": True,
                "backend": "nvidia",
                "details": "/dev/nvidia0 device node present.",
            }
    except Exception:
        pass

    nvidia_env = os.environ.get("NVIDIA_VISIBLE_DEVICES", "")
    if nvidia_env and nvidia_env.lower() not in ("none", "void", ""):
        return {
            "available": True,
            "backend": "nvidia",
            "details": f"NVIDIA_VISIBLE_DEVICES={nvidia_env}",
        }

    return {
        "available": False,
        "backend": "none",
        "details": "No GPU detected (nvidia-smi absent, /dev/nvidia0 missing, NVIDIA_VISIBLE_DEVICES unset).",
    }
