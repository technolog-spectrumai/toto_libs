"""The seam between the shared workspace base and its two language apps.

Ambrosia owns the workspace — the folder in a bucket, the tree, the tabs, the
file CRUD — and knows nothing about running anything. Antaresia (Python) and
texlab (LaTeX) each register here from their `AppConfig.ready()`, and the base
asks the registry instead of importing either app:

- which app answers for a workspace `kind` (routing, redirects, seeding);
- what extra room context and config URLs that kind contributes;
- how to tear down its runtime state when a workspace closes;
- which file is "main" for the explorer's tick;
- which settings that kind offers, and the panel section that renders them;
- which extra cards it contributes to the room itself;
- what it keeps when a workspace hibernates, and how to put it back.

That direction — the specialised apps know the base, never the reverse — is
what lets a build install one of them without the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


class RunRefused(Exception):
    """A run a person can do something about, phrased for them.

    Carries the same three things every refusal on this platform carries: the
    sentence, a machine-readable `code` a client branches on, and the HTTP
    status that says which KIND of refusal it is (403 you may not, 409 not in
    this state, 503 not right now).

    Defined in the registry rather than in either language app because the
    base has to catch it, and the base may not import them.
    """

    def __init__(self, message: str, *, code: str = "", status: int = 409):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _no_settings() -> tuple:
    return ()


@dataclass(frozen=True)
class WorkspaceApp:
    namespace: str          # the URL namespace; MUST match the mount in urls.py
    kind: str               # WorkspaceKind value this app owns
    extra_context: Callable # (request, workspace) -> dict merged into the room
    extra_urls: Callable    # (namespace, workspace) -> dict for config["urls"]
    teardown: Callable      # (workspace) -> None, on close/destroy
    main_id_for: Callable   # (workspace) -> int | None, the explorer's tick
    # What this kind lets a workspace configure, and how the panel renders it.
    # Both default to "nothing", so a language app that offers no settings —
    # or one written before this seam existed — registers unchanged and the
    # panel simply shows the shared sections.
    settings_fields: Callable = _no_settings   # () -> tuple[settings_spec.Field, ...]
    settings_template: str = ""                # template path for the panel section
    # Extra cards this kind contributes to the ROOM itself, as template paths,
    # rendered in order under the editor. `settings_template` was the only
    # injection point for a long time and it lands inside the settings DRAWER —
    # fine for a dial, wrong for a working surface like a package list, which
    # has to be visible while you edit rather than behind a slide-over.
    #
    # Templates rather than a component API on purpose: the base has no idea
    # what a Libraries panel contains, and should not grow one.
    room_panels: tuple = ()
    # Hibernation, and the reason both halves are hooks rather than code in the
    # base: the base has no idea what a Python runtime keeps or what a LaTeX one
    # would. A lab that offers neither registers unchanged and its workspaces
    # hibernate to a manifest with nothing lab-specific in it.
    #
    # `snapshot(workspace, permanent_home=bool)` -> dict, called BEFORE the
    # runtime is stopped, because a persistent home is read out of a live
    # container's output area. May return "home_files" ({name: bytes}), which
    # the base packs, hashes and stores.
    #
    # `restore(workspace, manifest=, home_files=, user=, capsule_uuid=)` -> None.
    snapshot: Optional[Callable] = None
    restore: Optional[Callable] = None
    # THE ONE VERB a workspace of this kind has: run the Python, compile the
    # LaTeX. A hook rather than two endpoints, for the reason every other line
    # in this dataclass is a hook — the base has no idea what running means
    # here, and a client should not have to know either. It asks a workspace
    # to do its thing and the registry decides what that is.
    #
    # `run(workspace, *, user, payload: dict) -> dict`, raising
    # `RunRefused` for anything a person can act on. `payload` is the client's
    # JSON body, so a language app can take what it needs from it (Python
    # takes `code`; LaTeX takes an optional `capsule`) without the base
    # growing a parameter per language.
    #
    # IT MUST DO THE METERING. The page views charge for a run, and an API
    # that reached the same compute without charging would be a paywall with
    # a second door. That is why the implementations are extracted from those
    # views rather than written beside them.
    #
    # None where a lab offers no run verb at all; the endpoint then 404s,
    # which is the honest answer for "this workspace cannot do that".
    run: Optional[Callable] = None
    # POLLING THE ANSWER, for a lab whose `run` only queues one.
    #
    # `poll(workspace, *, user, run_id) -> dict`, raising `RunRefused` — or a
    # 404 through the base — for a run that is not this workspace's.
    #
    # None where a run answers synchronously, and that is not an oversight:
    # dracena's Run IS the result, so there is nothing to come back for and an
    # endpoint that existed would only ever 404. A LaTeX compile takes tens of
    # seconds and returns a receipt, so texlab registers one.
    #
    # WITHOUT THIS A QUEUED ANSWER IS A DEAD END. `run` handed a client a run
    # id and there was no token-authenticated way to ask what became of it —
    # the page had `texlab:latex_run`, which needs a session. A desktop LaTeX
    # editor could start a compile and never learn whether it worked.
    poll: Optional[Callable] = None


_BY_NAMESPACE: dict[str, WorkspaceApp] = {}
_BY_KIND: dict[str, WorkspaceApp] = {}


def register(app: WorkspaceApp) -> None:
    _BY_NAMESPACE[app.namespace] = app
    _BY_KIND[app.kind] = app


def for_namespace(namespace: str) -> Optional[WorkspaceApp]:
    return _BY_NAMESPACE.get(namespace or "")


def all_apps() -> tuple:
    """Every registered language app. Order is not meaningful.

    Exists so callers can ask WHICH languages are mounted instead of naming
    them: a literal ``("dracena", "texlab")`` stops being true the moment one
    is renamed, and does so silently.
    """
    return tuple(_BY_NAMESPACE.values())


def for_kind(kind: str) -> Optional[WorkspaceApp]:
    return _BY_KIND.get(kind or "")


def kinds_installed() -> list[str]:
    """The kinds a workspace can be created as on this build."""
    return list(_BY_KIND)
