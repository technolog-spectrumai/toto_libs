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
    # `restore(workspace, manifest=, home_files=, user=, gear_uuid=)` -> None.
    snapshot: Optional[Callable] = None
    restore: Optional[Callable] = None


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
