"""Prove the INSTALLED wheel (not this checkout) satisfies Django imports.

Runs from a temp cwd with only that dir on PYTHONPATH, so the repository's
toto/ source tree cannot shadow site-packages.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def _clean_env(tmp_path):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BUILD_", "INSTALL_", "SABBIA_", "DJANGO_", "PYTHON"))
    }
    env["PYTHONPATH"] = str(tmp_path)
    return env


def test_toto_resolves_from_site_packages(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", "import toto; print(toto.__file__)"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "site-packages" in result.stdout, result.stdout


def test_django_check_passes_from_wheel(tmp_path):
    shutil.copy(TESTS_DIR / "settings_min.py", tmp_path / "settings_min.py")
    shutil.copy(TESTS_DIR / "urls_min.py", tmp_path / "urls_min.py")
    result = subprocess.run(
        [sys.executable, "-m", "django", "check", "--settings=settings_min"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
