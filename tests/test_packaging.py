"""Wheel-content assertions: everything the hosts rely on must ship."""
import glob
import os
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Apps that intentionally have no migrations (non-model / base apps).
NO_MIGRATION_APPS = {"editor", "neo_editor", "sso_core", "steven"}
# Non-app packages inside toto/ (no AppConfig, no migrations expected).
NON_APP_PACKAGES = {"ui", "ingress"}


def _wheel_path() -> Path:
    override = os.environ.get("TOTO_WHEEL")
    if override:
        return Path(override)
    candidates = sorted(glob.glob(str(REPO_ROOT / "dist" / "toto-*.whl")), key=os.path.getmtime)
    if not candidates:
        pytest.fail("no wheel found — build one into dist/ first (scripts/clean_env_check.sh)")
    return Path(candidates[-1])


@pytest.fixture(scope="module")
def wheel_names():
    with zipfile.ZipFile(_wheel_path()) as zf:
        return zf.namelist()


@pytest.fixture(scope="module")
def wheel_zip():
    with zipfile.ZipFile(_wheel_path()) as zf:
        yield zf


def test_version_is_single_sourced(wheel_zip, wheel_names):
    init = wheel_zip.read("toto/__init__.py").decode()
    assert '__version__ = "0.2.0"' in init
    metadata_name = next(n for n in wheel_names if n.endswith(".dist-info/METADATA"))
    metadata = wheel_zip.read(metadata_name).decode()
    assert "Name: toto" in metadata
    assert "Version: 0.2.0" in metadata


def test_migrations_are_packaged(wheel_names):
    apps_with_migrations = {
        name.split("/")[1]
        for name in wheel_names
        if name.startswith("toto/") and name.endswith("/migrations/__init__.py")
    }
    assert len(apps_with_migrations) == 42, sorted(apps_with_migrations)
    assert not apps_with_migrations & NO_MIGRATION_APPS
    # A representative initial migration with real operations rides along.
    assert "toto/core/migrations/0001_initial.py" in wheel_names


def test_templates_are_packaged(wheel_names):
    templates = [n for n in wheel_names if "/templates/" in n]
    assert len(templates) >= 241, len(templates)
    # Regression: the old glob (templates/**/*.html) dropped this .txt template.
    assert "toto/sso_master/templates/sso/password_reset_subject.txt" in wheel_names


def test_static_and_wasm_are_packaged(wheel_names):
    static = [n for n in wheel_names if "/static/" in n]
    assert len(static) >= 11, static
    assert "toto/telegraph/static/js/rotor_wasm/rotor_wasm_bg.wasm" in wheel_names
    assert "toto/telegraph/static/js/rotor_wasm/rotor_wasm.js" in wheel_names


def test_graph_yaml_are_packaged(wheel_names):
    yamls = [n for n in wheel_names if "/graph/" in n and n.endswith(".yaml")]
    assert len(yamls) == 6, yamls


def test_management_commands_are_packaged(wheel_names):
    assert "toto/core/management/commands/init_data.py" in wheel_names
    assert "toto/core/management/commands/create_platform.py" in wheel_names
    assert "toto/mandragora/management/commands/run_kernel_server.py" in wheel_names


def test_host_api_modules_are_packaged(wheel_names):
    for module in ("conf", "features", "registry", "routing", "schedules", "celery_utils"):
        assert f"toto/{module}.py" in wheel_names, module


def test_no_foreign_payload(wheel_names):
    assert not [n for n in wheel_names if n.startswith("limbo/")]
    assert not [n for n in wheel_names if n.startswith("rotors/")]
    assert not [n for n in wheel_names if "core/static/vendor/" in n]
    assert not [n for n in wheel_names if "__pycache__" in n]
