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


# test_payload_matches_the_pre_split_baseline was retired here, exactly as its own
# docstring instructed ("retire this test the first time the suite legitimately gains or
# drops a file"). The telegraph → forum rework did both: it dropped vault.py, two vault
# management commands and the rotor WASM bundle, and added store.py, search.py and
# permissions.py. The checks below are the durable invariants; the frozen file list was
# only ever proof that the one-off package split was lossless.


def test_migrations_are_packaged(all_names, owner):
    apps_with_migrations = {
        name.split("/")[1]
        for name in all_names
        if name.startswith("toto/") and name.endswith("/migrations/__init__.py")
    }
    assert len(apps_with_migrations) == 35, sorted(apps_with_migrations)
    assert not apps_with_migrations & NO_MIGRATION_APPS
    # A representative initial migration with real operations rides along.
    assert owner.get("toto/core/migrations/0001_initial.py") == "toto-base"


def test_gis_off_migration_graph_is_packaged(owner):
    # The BUILD_GEO=0 host selects this alternate locations graph via
    # MIGRATION_MODULES; it must ride in the toto-base wheel next to the GIS-on one.
    assert owner.get("toto/locations/migrations_nogis/__init__.py") == "toto-base"
    assert owner.get("toto/locations/migrations_nogis/0001_initial.py") == "toto-base"
    assert owner.get("toto/locations/migrations/0005_address_latlon.py") == "toto-base"


def test_templates_are_packaged(all_names, owner):
    templates = [n for n in all_names if "/templates/" in n]
    assert len(templates) >= 204, len(templates)
    # Regression: the old glob (templates/**/*.html) dropped this .txt template.
    assert owner.get("toto/sso_master/templates/sso/password_reset_subject.txt") == "toto-auth"
    # The shared base template every app extends.
    assert owner.get("toto/core/templates/oya/base.html") == "toto-base"


def test_static_and_wasm_are_packaged(all_names, owner):
    static = [n for n in all_names if "/static/" in n]
    assert len(static) >= 5, static
    # The rotor WASM assertions that used to live here went away with the forum app's
    # MLS encryption; toto-chat now ships no static files.
    assert not [n for n in all_names if n.startswith("toto/forum/static/")]
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


def test_repackaged_apps_ship_in_their_new_homes(owner):
    # weather -> toto-geo (v1.6); kanban, memo -> toto-works (v1.6).
    assert owner.get("toto/weather/models.py") == "toto-geo"
    assert owner.get("toto/kanban/migrations/0001_initial.py") == "toto-works"
    assert owner.get("toto/memo/models.py") == "toto-works"


def test_auth_apps_ship_in_toto_auth(owner):
    # sso_core, sso_master, sso_client -> toto-auth (v1.8).
    assert owner.get("toto/sso_core/manifest.py") == "toto-auth"
    assert owner.get("toto/sso_master/migrations/0001_initial.py") == "toto-auth"
    assert owner.get("toto/sso_client/models.py") == "toto-auth"
    # The strategy resolver + local-mode url aliases ride with the apps.
    assert owner.get("toto/auth_config.py") == "toto-auth"
    assert owner.get("toto/auth_local_urls.py") == "toto-auth"
    # Social login (google/facebook) ships with the auth package too.
    assert owner.get("toto/social_login/migrations/0001_initial.py") == "toto-auth"
    assert owner.get("toto/social_login/templates/social_login/_login_buttons.html") == "toto-auth"


def test_media_apps_ship_in_toto_media(owner):
    # The video/media apps live in the optional toto-media package, not toto-base
    # or toto-works — a regression here means a mover drifted back.
    assert owner.get("toto/vod/migrations/0002_drop_all_vod_tables.py") == "toto-media"
    assert owner.get("toto/transcription/migrations/0001_initial.py") == "toto-media"
    assert owner.get("toto/manta/templates/manta/command_builder.html") == "toto-media"
    assert owner.get("toto/fileservices/models.py") == "toto-media"


def test_no_foreign_payload(all_names):
    assert not [n for n in all_names if n.startswith("limbo/")]
    assert not [n for n in all_names if n.startswith("rotors/")]
    assert not [n for n in all_names if "core/static/vendor/" in n]
    assert not [n for n in all_names if "__pycache__" in n]
    assert not [n for n in all_names if not n.startswith("toto/")]
