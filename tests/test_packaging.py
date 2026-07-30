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
    # 38 as of 1.21: 37 plus toto.datalink, a new toto-base app with its own tables
    # (peer grants, runs, merge bases). Before that it was 37, since assets + tariffs
    # were promoted out of the zenobia host into toto-economy (a second host needed to
    # price its own work). Moving an app between HOSTS never touches this; adding one
    # to a PACKAGE, or moving one into or out of one, does. The 1.21 media split moved
    # four apps between packages and so left the count alone: manta, fileservices and
    # transcription are packaged in toto-media-ops and ocr in toto-media, so all four
    # still ship. What WOULD change it is retiring one to limbo/, which is not a
    # package and never ships.
    assert len(apps_with_migrations) == 38, sorted(apps_with_migrations)
    assert not apps_with_migrations & NO_MIGRATION_APPS
    # A representative initial migration with real operations rides along.
    assert owner.get("toto/core/migrations/0001_initial.py") == "toto-base"
    # The promoted pair travels together: tariffs' initial migration depends on
    # assets', and splitting them across packages would be unbuildable.
    assert owner.get("toto/assets/migrations/0001_initial.py") == "toto-economy"
    assert owner.get("toto/tariffs/migrations/0001_initial.py") == "toto-economy"


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
    # It moved sso_master -> sso_core in 1.23 with the rest of the reset flow, so a
    # consumer host gets it too; still the only non-.html template in the suite,
    # which is what makes it the right canary for the glob.
    assert owner.get("toto/sso_core/templates/sso/password_reset_subject.txt") == "toto-auth"
    # sso_core had no templates directory at all before that move, so this also
    # pins that a newly-templated app is picked up by the packaging config.
    assert owner.get("toto/sso_core/templates/sso/password_reset.html") == "toto-auth"
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


def test_toto_media_ships_the_light_pair(payloads, owner):
    # toto-media is the LIGHT media package, the one hosts pin: vod and ocr, both
    # stateless, neither needing a worker. If an app that wants celery or a heavy
    # wheel lands back here, BUILD_MEDIA stops being safe for a lean WSGI host —
    # which was the whole point of the 1.21 split.
    #
    # ocr sits here rather than with the processing tier on purpose. Its only cost is
    # a small tesseract apt layer that no host is forced to build, which is a
    # different order of cost from torch, ffmpeg and a celery container. It also
    # could not have stayed in toto-graph, where it began: that package
    # hard-depends on toto-ai, so a host wanting OCR alone had no way to install it.
    apps = {n.split("/")[1] for n in payloads["toto-media"] if n.startswith("toto/")}
    assert apps == {"vod", "ocr"}, sorted(apps)
    assert owner.get("toto/vod/migrations/0002_drop_all_vod_tables.py") == "toto-media"
    assert owner.get("toto/vod/templates/vod/library.html") == "toto-media"
    assert owner.get("toto/ocr/templates/ocr/ocr.html") == "toto-media"
    assert owner.get("toto/ocr/migrations/0001_squashed_0002.py") == "toto-media"


def test_the_processing_tier_ships_in_toto_media_ops(payloads, owner):
    # manta, fileservices and transcription: the set that wants a celery worker.
    # Packaged, versioned and buildable — just pinned by no host; see that package's
    # README, and test_monorepo.py in the portal monorepo, which asserts the "no
    # host" half.
    apps = {n.split("/")[1] for n in payloads["toto-media-ops"] if n.startswith("toto/")}
    assert apps == {"manta", "fileservices", "transcription"}, sorted(apps)
    assert owner.get("toto/manta/templates/manta/command_builder.html") == "toto-media-ops"
    assert owner.get("toto/fileservices/models.py") == "toto-media-ops"
    # Written in 1.21 and never deployed; the app had no run index before it.
    assert owner.get("toto/fileservices/templates/fileservices/run_list.html") == "toto-media-ops"


def test_every_task_module_ships_a_tasks_submodule(owner):
    """Celery autodiscovery imports ``<label>.tasks`` and nothing else.

    A label in TASK_MODULES whose package has no ``tasks.py`` is silently inert:
    the producer registers the task by importing whatever module defines it, the
    worker never does, and the job is enqueued and rejected as unregistered with
    nothing pointing at the cause. ``toto.manta`` was exactly this — its task
    lives in ``tasks_direct.py`` and is *named* ``toto.manta.tasks.run_direct``,
    which is where the missing module was written down.
    """
    from toto.registry import TASK_MODULES

    for label in TASK_MODULES:
        app = label.split(".", 1)[1]
        assert owner.get(f"toto/{app}/tasks.py"), (
            f"{label} is in TASK_MODULES but no wheel ships toto/{app}/tasks.py — "
            "autodiscovery will find nothing and its jobs will never run"
        )


def test_datalink_ships_in_toto_base(owner, all_names):
    # It is a toto-base app so every host inherits the code, and whether it is INSTALLED
    # is a per-host BUILD_DATALINK decision. Its tables hold peer credentials, so the
    # migration must travel with it or a host that switches the flag on has no schema.
    assert owner.get("toto/datalink/migrations/0001_initial.py") == "toto-base"
    assert owner.get("toto/datalink/registry.py") == "toto-base"
    assert owner.get("toto/datalink/services/serialize.py") == "toto-base"
    # The per-app policy modules are what make the registry complete; a missing one is
    # a model nobody decided about.
    for app in ("core", "people", "locations", "socialhub", "events",
                "vault", "api", "gervazy", "backup"):
        assert owner.get(f"toto/{app}/datalink_policies.py") == "toto-base", app
    # The two-instance harness ships too — every host's clean-env gate runs it.
    assert owner.get("toto/datalink/federation/settings.py") == "toto-base"
    assert [n for n in all_names if n.startswith("toto/datalink/federation/tests/")]


def test_the_media_sub_nav_ships_in_toto_base(owner):
    # Not in vod or ocr, deliberately: Django only loads template dirs for
    # INSTALLED apps, so a shared partial living in either app would vanish
    # exactly when that app is switched off — which is what BUILD_VOD and
    # BUILD_OCR being independent makes routine.
    #
    # The path matters as much as the package. This started life at
    # toto/core/templates/media/_tabs.html and shipped in nothing, because
    # .gitignore's `media/` rule (for MEDIA_ROOT) matches at any depth and quietly
    # kept it out of every commit. This assertion is what caught that.
    assert owner.get("toto/core/templates/oya/_media_tabs.html") == "toto-base"
    assert not [n for n in owner if "/templates/media/" in n]


def _requires(wheel_path):
    """The Requires-Dist names a wheel declares.

    Parsed from the headers only, NOT by searching the whole METADATA blob: the
    long_description is appended to it, so a package whose README merely *mentions*
    a sibling would otherwise read as depending on it. toto-media's README does
    exactly that, which is how this was found.
    """
    with zipfile.ZipFile(wheel_path) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        metadata = zf.read(name).decode()
    headers = metadata.split("\n\n", 1)[0]      # blank line ends the headers
    return {
        line.split(":", 1)[1].split("==")[0].split(";")[0].strip()
        for line in headers.splitlines()
        if line.startswith("Requires-Dist:")
    }


def test_nothing_in_the_suite_depends_on_toto_media_ops(wheels):
    # The library-side half of "installed nowhere": no other package may require it,
    # or pinning any of them would drag ffmpeg and the whisper stack in
    # transitively. (The host-side half — that no requirements.toto.txt names it —
    # lives in the portal monorepo's scripts/test_monorepo.py.)
    for name, path in wheels.items():
        if name == "toto-media-ops":
            continue
        assert "toto-media-ops" not in _requires(path), f"{name} requires toto-media-ops"


def test_toto_media_requires_only_toto_base(wheels):
    # It carries vod and ocr, neither of which has a model, a task or a sibling
    # import beyond toto-base. The toto-flow dependency it had until 1.21 left with
    # fileservices, and that is what lets a lean host pin this wheel.
    assert _requires(wheels["toto-media"]) == {"toto-base"}


def test_no_foreign_payload(all_names):
    assert not [n for n in all_names if n.startswith("limbo/")]
    assert not [n for n in all_names if n.startswith("rotors/")]
    assert not [n for n in all_names if "core/static/vendor/" in n]
    assert not [n for n in all_names if "__pycache__" in n]
    assert not [n for n in all_names if not n.startswith("toto/")]
