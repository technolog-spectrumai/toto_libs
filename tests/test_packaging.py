"""Wheel-content assertions: everything the hosts rely on must ship.

toto is several distributions sharing the ``toto.*`` namespace, so each check
names the package expected to carry the payload — that is what keeps a file
from silently moving between packages when apps are reshuffled.
"""
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Apps that intentionally have no migrations (non-model / base apps).
NO_MIGRATION_APPS = {"editor", "neo_editor", "sso_core", "steven"}
# Non-app packages inside toto/ (no AppConfig, no migrations expected).
NON_APP_PACKAGES = {"ui", "ingress"}
# The shared host API every host imports; all of it lives in toto-base.
HOST_API_MODULES = ("conf", "features", "registry", "routing", "schedules",
                    "celery_utils", "versioning")


def test_every_package_declares_the_suite_version(wheels):
    expected = (REPO_ROOT / "VERSION").read_text().strip()
    for name, path in wheels.items():
        with zipfile.ZipFile(path) as zf:
            metadata_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
            metadata = zf.read(metadata_name).decode()
        assert f"Name: {name}" in metadata, name
        assert f"Version: {expected}" in metadata, f"{name} is not at {expected}"


def test_namespace_has_no_init(all_names):
    """A toto/__init__.py in any wheel would break the PEP 420 namespace."""
    assert "toto/__init__.py" not in all_names


def test_packages_do_not_overlap(payloads):
    """No file may ship in two wheels: pip would install whichever landed last."""
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for package, entries in sorted(payloads.items()):
        for entry in entries:
            if entry in seen:
                clashes.append(f"{entry}: {seen[entry]} and {package}")
            seen[entry] = package
    assert not clashes, clashes


def test_payload_matches_the_pre_split_baseline(all_names):
    """The split must not lose (or invent) a single packaged file.

    The baseline is the payload of the last single-distribution wheel. Retire
    this test the first time the suite legitimately gains or drops a file —
    until then it is the proof that the repackaging was lossless.
    """
    baseline = set((REPO_ROOT / "tests" / "data" / "wheel_payload_baseline.txt").read_text().split())
    baseline.discard("toto/__init__.py")          # deliberately removed by the split
    expected = baseline | {f"toto/{m}.py" for m in HOST_API_MODULES}
    assert sorted(expected - all_names) == [], "payload lost by the split"
    assert sorted(all_names - expected) == [], "payload gained by the split"


def test_migrations_are_packaged(all_names, owner):
    apps_with_migrations = {
        name.split("/")[1]
        for name in all_names
        if name.startswith("toto/") and name.endswith("/migrations/__init__.py")
    }
    assert len(apps_with_migrations) == 43, sorted(apps_with_migrations)
    assert not apps_with_migrations & NO_MIGRATION_APPS
    # A representative initial migration with real operations rides along.
    assert owner.get("toto/core/migrations/0001_initial.py") == "toto-base"


def test_templates_are_packaged(all_names, owner):
    templates = [n for n in all_names if "/templates/" in n]
    assert len(templates) >= 241, len(templates)
    # Regression: the old glob (templates/**/*.html) dropped this .txt template.
    assert owner.get("toto/sso_master/templates/sso/password_reset_subject.txt") == "toto-base"
    # The shared base template every app extends.
    assert owner.get("toto/core/templates/oya/base.html") == "toto-base"


def test_static_and_wasm_are_packaged(all_names, owner):
    static = [n for n in all_names if "/static/" in n]
    assert len(static) >= 11, static
    # The rotor WASM is synced into the checkout by the hosts' deploy.py and
    # must ride inside whichever package owns telegraph.
    assert owner.get("toto/telegraph/static/js/rotor_wasm/rotor_wasm_bg.wasm") == "toto-chat"
    assert owner.get("toto/telegraph/static/js/rotor_wasm/rotor_wasm.js") == "toto-chat"
    assert owner.get("toto/core/static/oya/alpine.js") == "toto-base"


def test_graph_yaml_are_packaged(all_names, owner):
    yamls = [n for n in all_names if "/graph/" in n and n.endswith(".yaml")]
    assert len(yamls) == 6, yamls
    assert all(owner[y] == "toto-graph" for y in yamls), {y: owner[y] for y in yamls}


def test_management_commands_are_packaged(owner):
    assert owner.get("toto/core/management/commands/init_data.py") == "toto-base"
    assert owner.get("toto/core/management/commands/create_platform.py") == "toto-base"
    assert owner.get("toto/mandragora/management/commands/run_kernel_server.py") == "toto-flow"


def test_host_api_modules_ship_in_base(owner):
    for module in HOST_API_MODULES:
        assert owner.get(f"toto/{module}.py") == "toto-base", module
    assert owner.get("toto/ui/__init__.py") == "toto-base"
    assert owner.get("toto/ingress/__init__.py") == "toto-base"


def test_no_foreign_payload(all_names):
    assert not [n for n in all_names if n.startswith("limbo/")]
    assert not [n for n in all_names if n.startswith("rotors/")]
    assert not [n for n in all_names if "core/static/vendor/" in n]
    assert not [n for n in all_names if "__pycache__" in n]
    assert not [n for n in all_names if not n.startswith("toto/")]
