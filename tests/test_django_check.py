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
    # toto is a PEP 420 namespace shared by several distributions: it has no
    # __file__, only __path__. A non-None __file__ means a pre-split 'toto'
    # wheel is installed and shadowing the suite.
    result = subprocess.run(
        [sys.executable, "-c", "import toto; print(toto.__file__); print(*toto.__path__, sep='\\n')"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    first, *paths = result.stdout.splitlines()
    assert first == "None", f"toto.__file__ is {first} — a legacy 'toto' distribution is installed"
    assert paths and all("site-packages" in p for p in paths), result.stdout


def test_installed_suite_is_coherent(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c",
         "from toto.versioning import check_runtime_coherence, installed_suite;"
         " check_runtime_coherence(); print(sorted(installed_suite().items()))"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
