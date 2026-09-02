"""Wheel-content assertions: everything the hosts rely on must ship.

toto is several distributions sharing the ``toto.*`` namespace, so each check
names the package expected to carry the payload — that is what keeps a file
from silently moving between packages when apps are reshuffled.
"""
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Apps that intentionally have no migrations (non-model / base apps).
NO_MIGRATION_APPS = {"editor", "neo_editor", "sso_core"}
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
    # 40 as of 1.29: 39 plus toto.primula, a toto-works app with its own table (the
    # sheet version history). 39 came in 1.25 with toto.jess, a new toto-base app with
    # its own tables (the provider config and the outbox). 38 came in 1.21 with toto.datalink, and 37 before
    # that, when assets + tariffs were promoted out of the zenobia host into
    # toto-economy (a second host needed to price its own work). Moving an app between
    # HOSTS never touches this; adding one to a PACKAGE, or moving one into or out of
    # one, does. The 1.21 media split moved four apps between packages and so left the
    # count alone: manta, fileservices and transcription are packaged in toto-media-ops
    # and ocr in toto-media, so all four still ship. What WOULD change it is retiring
    # one to limbo/, which is not a package and never ships.
    # 41 as of 1.29: 40 plus toto.clearing, the toto-economy app that federates
    # selected assets with a paired platform (its own tables: the peer, and the
    # bridge state that follows in later stages).
    # 40 as of 1.45: antaresia was DELETED from toto-works (not moved) — its
    # name now belongs to zenobia's Python workspace host app, ambrosia's
    # successor, and its PythonRun table dies with it.
    # 43 as of 1.50, and the jump from 40 is three apps, not one:
    #   * toto.tax     (8/2026, toto-economy) — the levy engine's own tables
    #   * toto.cyprian (8/2026, toto-works)   — gained a quota/usage pair
    #   * toto.gitvault(1.50,  toto-flow)     — came IN from the zenobia host
    #     (the repo, the run, the quota pair, the custom remote). It was
    #     host-owned while only zenobia had surfaces to version; the workspace
    #     apps moved to placidia and versioning had to follow them, which a
    #     host app cannot do. The app LABEL is unchanged, so no deployed
    #     database notices the move.
    # The first two drifted in unnoticed because dist/ held stale wheels: this
    # assertion only bites once the wheels are rebuilt, so rebuild before
    # trusting it.
    # 44: toto.mint (toto-economy) — where currencies are engraved, minted and
    # burned, and where the append-only chain of monetary events lives. It is
    # installed on the MASTER only, but it SHIPS in the wheel like every other
    # app here; what keeps a branch from minting is that the branch does not
    # install it and holds no issuer key.
    # 45: toto.gitvault SPLIT into toto.repo + toto.gitea and moved to the new
    # toto-repo wheel. A package→package MOVE would have left this alone (see
    # the 1.21 media split above); it is the split that costs one, because one
    # app label became two. Zenobia installs the gitea half, placidia the repo
    # half, and neither installs both — which one flag could not express and is
    # the whole reason for the change.
    # 46: toto.antivirus (toto-base) — the scan verdict cache and the per-user
    # "which types are screened automatically" row. Note what did NOT move this
    # number in the same release: toto.sketch shipped in toto-works alongside it
    # and is not counted, because it has no models at all (the vault file IS the
    # drawing) and so carries no migrations package. An empty one arrived with it
    # from delta and was deleted — boilerplate that would have inflated exactly
    # this tripwire.
    # 47: toto.subscriptions (toto-base) — plans, the community discount, one
    # subscription per user and one row per month. It arrived in the same
    # release that DELETED two things which were never counted here, because
    # neither owned an app: the head tax was a metric plus a levy provider
    # inside socialhub, and the company tribute was two models inside zenobia's
    # host-owned portfolio, which does not ship in any wheel.
    # 48: toto.steven (toto-ai) — the assistant's provider row and its run log.
    # The app existed before and was NOT counted: it was 235 lines of floating
    # chat widget with no models at all, which is why it appears in
    # NO_MIGRATION_APPS at the top of this file. Rewriting it gave it tables, so
    # that exemption went with the rewrite.
    # 49: toto.polls (toto-base) — came IN from the zenobia host, where it had
    # always lived. The Forum has to show the polls belonging to a room and the
    # poll stays owned by the Polls app; forum ships in toto-chat, and a wheel
    # cannot import a host portion, so the app had to move to be reachable at
    # all. Same move, and the same reason, as cyprian in 1.41 and gitvault in
    # 1.50. The app LABEL is unchanged, so no deployed database notices.
    # 48 again: toto.datalink went OUT — parked to limbo/ with the sealing
    # decision that made the vault's bucket peer API the platform's one
    # host-to-host data channel. It had migrations but was installed by no
    # host, so no deployed database notices this either.
    # 49 again: toto.mail (toto-base) — real per-user mailboxes on real IMAP/
    # SMTP servers, with the connection, the sealed credential, the attachment
    # rows and one sent-record per recipient. It is NOT jess: jess is the
    # platform's own outbox for transactional mail, and this is people's
    # accounts, which is why it owns tables rather than extending that app's.
    # 47 now: toto.hesperis went OUT — parked to limbo/ on 2026-08-29 and
    # succeeded by toto.lacedo, a HOST portion on zenobia, which is why the
    # replacement does not come back into this count. It took its six models
    # and the toto-economy edge with it; see limbo/hesperis/PARKED.md.
    # 48 before that: toto.polls went OUT — parked to limbo/ when the Forum stopped
    # borrowing it and started owning its polls. It came in (entry 49 above) so
    # that a wheel could reach it at all; a room's poll is a `forum` model with
    # a real ForeignKey to its channel now, so the app it was reached FOR no
    # longer needs it. Its tables are deliberately left in place, and the quiz
    # desk it carried is parked with it — see limbo/polls/PARKED.md.
    assert len(apps_with_migrations) == 47, sorted(apps_with_migrations)
    assert not apps_with_migrations & NO_MIGRATION_APPS
    # A representative initial migration with real operations rides along.
    assert owner.get("toto/core/migrations/0001_initial.py") == "toto-base"
    # The promoted pair travels together: tariffs' initial migration depends on
    # assets', and splitting them across packages would be unbuildable.
    assert owner.get("toto/assets/migrations/0001_initial.py") == "toto-economy"
    assert owner.get("toto/tariffs/migrations/0001_initial.py") == "toto-economy"
    # Clearing rides the same wheel: it imports the ledger directly and the
    # partition must keep the pair together.
    assert owner.get("toto/clearing/migrations/0001_initial.py") == "toto-economy"
    assert owner.get("toto/mint/migrations/0001_initial.py") == "toto-economy"
    assert owner.get("toto/clearing/testing/settings.py") == "toto-economy"


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


def test_plan_yaml_is_packaged(owner):
    """The subscription ladder is DATA, and a wheel that drops it does not
    boot: `SubscriptionsConfig.ready` loads the file and raises PlanError when
    it is missing, so every page 500s on a host installed from wheels only.
    That is exactly what the clean-env gate installs, and exactly the failure
    a source checkout cannot reproduce.

    PLANS.md and the example ride along: an operator asked to point
    SUBSCRIPTION_PLANS_FILE somewhere needs the schema on the host they are
    holding, not in a repository they may not have.
    """
    assert owner.get("toto/subscriptions/plans.yaml") == "toto-base"
    assert owner.get("toto/subscriptions/plans.example.yaml") == "toto-base"
    assert owner.get("toto/subscriptions/PLANS.md") == "toto-base"


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
    # manifest.py went in 1.26 with the connection-bundle flow it served; the
    # pairing modules that replaced it are asserted in
    # test_federation_pairing_ships_in_toto_auth.
    assert owner.get("toto/sso_core/enrollment.py") == "toto-auth"
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


def test_toto_repo_ships_both_apps_with_their_assets(payloads, owner):
    """toto.repo is useless as Python alone — its UI is a template and a script.

    It came from a HOST, where templates and static are just files on disk that
    always exist. In a wheel they ship only if setuptools is told to include
    them, and a missing one fails at render time on a deployed host rather than
    in any import. The files below are the whole user-facing surface: the modal
    host, the toolbar entry, the landing page, and the Alpine component that
    drives all three.
    """
    assert owner.get("toto/repo/git_cli.py") == "toto-repo"
    assert owner.get("toto/repo/templates/repo/_git_ui.html") == "toto-repo"
    assert owner.get("toto/repo/templates/repo/_git_toolbar_buttons.html") == "toto-repo"
    assert owner.get("toto/repo/templates/repo/index.html") == "toto-repo"
    assert owner.get("toto/repo/static/repo/git.js") == "toto-repo"
    assert owner.get("toto/repo/migrations/0001_initial.py") == "toto-repo"
    # The other half of the same wheel, and the reason there are two app labels:
    # a host installs whichever it can use, and zenobia's is this one.
    assert owner.get("toto/gitea/client.py") == "toto-repo"
    assert owner.get("toto/gitea/templates/gitea/index.html") == "toto-repo"
    assert owner.get("toto/gitea/migrations/0001_initial.py") == "toto-repo"


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


def test_federation_pairing_ships_in_toto_auth(owner, all_names):
    """Pairing spans both federation modes, so its pieces sit in sso_core.

    sso_core is the only auth app installed in BOTH provider and consumer mode, so
    it is the one place code both sides need can live without either app importing
    the other. It still ships no migrations — the models stay in sso_master and
    sso_client — which test_migrations_are_packaged asserts by app count.
    """
    for module in ("enrollment", "qr", "vault"):
        assert owner.get(f"toto/sso_core/{module}.py") == "toto-auth", module

    # The QR page and the join page are templates, which is the thing a wheel
    # silently drops.
    assert owner.get("toto/sso_master/templates/admin/sso_master/pair.html") == "toto-auth"
    assert owner.get("toto/sso_client/templates/admin/sso_client/federate.html") == "toto-auth"

    # The provider suite's runnable settings, so a host's gate can run it at all.
    assert owner.get("toto/sso_master/testing/settings.py") == "toto-auth"
    assert owner.get("toto/sso_master/testing/urls.py") == "toto-auth"

    # And the manifest path this replaced is gone from the wheel, not merely
    # unreferenced.
    assert not owner.get("toto/sso_core/manifest.py")
    assert not [n for n in all_names if n.endswith("ingress_sso_client.py")]


def test_jess_ships_in_toto_base(owner, all_names):
    """Jess is a toto-base app on a per-host BUILD_JESS flag.

    In toto-base rather than a wheel of its own because absorbing ``api.EmailService``
    meant deleting an FK target that ``socialhub.Community`` referenced — both of those
    are toto-base apps, so the RemoveField and the DeleteModel stay inside one migration
    graph. And ``core/email_config.py``, the thing Jess makes truthful, is here too.
    """
    assert owner.get("toto/jess/models.py") == "toto-base"
    # The two things a wheel silently drops.
    assert owner.get("toto/jess/migrations/0001_initial.py") == "toto-base"
    assert owner.get("toto/jess/templates/jess/message_detail.html") == "toto-base"
    # tasks.py by name — see test_every_task_module_ships_a_tasks_submodule for why.
    assert owner.get("toto/jess/tasks.py") == "toto-base"
    # The vault helper and the command that proves the passphrase opens the strongbox.
    assert owner.get("toto/jess/vault.py") == "toto-base"
    assert owner.get("toto/jess/management/commands/jess_init_vault.py") == "toto-base"
    # The suite's own runnable settings, so a host's clean-env gate can run it.
    assert owner.get("toto/jess/testing/settings.py") == "toto-base"
    assert owner.get("toto/jess/tests.py") == "toto-base"
    # The destructive absorption travels with the code, or a host that migrates has a
    # schema the models no longer describe.
    assert owner.get("toto/api/migrations/0004_delete_emailservice.py") == "toto-base"
    assert owner.get(
        "toto/socialhub/migrations/0002_remove_community_email_service.py"
    ) == "toto-base"
    # And the model it replaced is gone from the wheel, not merely unreferenced.
    assert not [n for n in all_names if n.endswith("toto/api/email_service.py")]


def test_memo_ships_in_toto_works(owner):
    """memo is the first app in this package to ship static files.

    Its `static/memo/*.css` and `*.js` reach a wheel through the
    ``package-data`` glob and the MANIFEST.in extension allowlist — and since
    ``build_wheels.py --sdist`` builds the wheel FROM the sdist, an extension
    missing from that allowlist would work locally and vanish only in a host's
    clean-env gate. Pin them here so the failure is one line instead.
    """
    assert owner.get("toto/memo/presentation_format.py") == "toto-works"
    assert owner.get("toto/memo/sanitize.py") == "toto-works"
    # The three things a wheel silently drops: templates, static, and the
    # settings module the gate needs to run the suite at all.
    assert owner.get("toto/memo/templates/memo/edit.html") == "toto-works"
    assert owner.get("toto/memo/templates/memo/_slide.html") == "toto-works"
    assert owner.get("toto/memo/static/memo/slide.css") == "toto-works"
    assert owner.get("toto/memo/static/memo/editor.js") == "toto-works"
    assert owner.get("toto/memo/testing/settings.py") == "toto-works"
    assert owner.get("toto/memo/tests.py") == "toto-works"
    assert owner.get("toto/memo/bundle.py") == "toto-works"
    assert owner.get("toto/memo/render_pdf.py") == "toto-works"


def test_primula_ships_in_toto_works(owner):
    """Primula is a toto-works app on a per-host BUILD_PRIMULA flag, beside memo.

    In toto-works because it is the same shape as memo — a standalone UI over a vault
    file type — and its only model FKs into ``vault.VaultFile``, which every host
    already installs. The Univer JS itself is NOT in any wheel: it is downloaded into
    ``core/static/vendor/univer/`` by each host's download_vendor.py at image build.
    """
    assert owner.get("toto/primula/models.py") == "toto-works"
    # The two things a wheel silently drops.
    assert owner.get("toto/primula/migrations/0001_initial.py") == "toto-works"
    assert owner.get("toto/primula/templates/primula/edit.html") == "toto-works"
    # The vault "open" routing and the seeder.
    assert owner.get("toto/primula/plugins/vault_editor_plugins.py") == "toto-works"
    assert owner.get("toto/primula/management/commands/ingress_primula.py") == "toto-works"
    # The suite's own runnable settings, so a host's clean-env gate can run it.
    assert owner.get("toto/primula/testing/settings.py") == "toto-works"
    assert owner.get("toto/primula/tests.py") == "toto-works"
    # The 'sheet' choice lives in vault (toto-base) — its migration must ride there.
    assert owner.get("toto/vault/migrations/0010_alter_vaultfile_file_type.py") == "toto-base"


def test_the_pxml_deck_class_ships_in_the_right_wheels(owner):
    """The deck file class straddles two packages, and both halves are droppable.

    The 'pxml' choice and its migration live in vault (toto-base); the plugin
    that gives a deck its Play button lives in memo (toto-works). The migration
    inlines a frozen copy of memo's deck sniff precisely so toto-base need not
    import toto-works — placidia installs the vault with no toto-works on disk
    at all, so an import there is a hard crash at migrate time.
    """
    assert owner.get("toto/vault/migrations/0021_pxml_file_type.py") == "toto-base"
    # A directory with no __init__.py: it ships only because toto-works sets
    # namespaces = true, which is exactly the kind of thing a wheel drops.
    assert owner.get("toto/memo/plugins/vault_play_plugins.py") == "toto-works"


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
