"""Putting a workspace to sleep, and waking it up.

## What hibernation is here

Compute goes to zero; everything else is written down so it can be rebuilt.

    drain → stop the runtime → RELEASE the lease → write a manifest
    …later…
    reserve → mount → verify the manifest → restore → open the room

**Release, not unmount.** ``services.booked()`` is "the sum of every OPEN lease,
mounted or not", so unmounting frees nothing at all — the reservation goes on
holding cpu, ram, scratch and pids in the pool until it is released. Zero
allocation therefore means releasing, and that has a consequence the UI has to
be honest about: **rehydration is not guaranteed.** Capacity goes back to a pool
other people draw on, and waking up issues a *fresh* reservation that can be
refused.

## Cold, not hot

The kernel is STOPPED before anything is captured. In-memory state — variables,
loaded dataframes, open handles — does not survive, and a rehydrated runtime
starts empty. What comes back is the *environment*, not the session: packages,
files, configuration, and, on a permanent-home Capsule, everything under ``$HOME``.

There is no CRIU-style process checkpoint, no live layer capture, and no live
network toggling. "Your setup comes back", not "your session resumes".

## Two depths, chosen when the Capsule was made

* **Manifest** — the default. Base image, runtime versions, the declared trees
  a lab knows how to rebuild, the vault snapshot, and where the user was.
* **Hybrid** — a Capsule reserved with ``permanent_home``. All of the above plus a
  collected ``$HOME``: shell history, tool configuration, an interactive
  ``pip --user`` install. It costs a blob per workspace and captures the state
  nobody declared.

A third, **layer** hibernation — freezing the container's writable filesystem —
is deliberately not built. With ``--read-only`` there is no writable layer to
freeze, and with ``--user 65534 --cap-drop ALL`` making the rootfs writable
would still leave every interesting path unwritable. It would take running
runners as root, which is a larger concession than the feature is worth.

## Generic by construction

This module names no lab. A language app contributes ``snapshot`` and
``restore`` through :class:`~toto.ambrosia.registry.WorkspaceApp`, exactly as it
already contributes settings and room panels, so a future Anastasia runtime
needs no change here.
"""

from __future__ import annotations

import hashlib
import io
import logging
import tarfile

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import registry

log = logging.getLogger("toto.ambrosia")

#: TWO CEILINGS, BECAUSE THERE ARE TWO SIZES, and conflating them is how a
#: home that passes the check becomes one that can never be restored.
#:
#: `MAX_HOME_BYTES` bounds the GZIPPED BLOB — what is stored, transferred and
#: held in memory here. `MAX_HOME_RAW_BYTES` bounds the UNCOMPRESSED TOTAL,
#: which is what the manager's staging budget actually measures: it enforces
#: `max_bytes` while unpacking, member by member, on real bytes written.
#:
#: MEASURED 2026-09-11: twenty ordinary csv files, 286 MB of text, gzip to a
#: 0.7 MB blob — 411:1. That passed the old 64 MB check comfortably and then
#: exceeded the 256 MB staging budget at every single wake-up. Which is
#: precisely the failure this ceiling was written to prevent: "kept and never
#: staged back — refused while somebody is watching, rather than at every
#: start afterwards". Checking only the compressed size was checking the one
#: number the restore path does not look at.
#:
#: RAISED FROM 64 MB, and derived rather than typed: an installed environment
#: does not fit in 64 MB, and the honest maximum is what can be staged back.
#: `_staging_budget` reads the manager's own constant so the two cannot drift
#: — a number copied here would be a number that goes stale silently.
#:
#: WHY IT DOES NOT GO HIGHER. `pack` builds the whole archive in a BytesIO and
#: `unpack` reads the whole blob back the same way, so the web process holds
#: several full copies of a home while it works. Raising this past the staging
#: budget would need a streaming path, not a bigger number — that is the real
#: cost of a bigger home, and it is why this is a derivation and not a knob.
def _staging_budget() -> int:
    """The manager's input budget, or a safe default when it is not installed.

    Read at call time rather than imported at module scope: ambrosia must load
    on a host that does not build anastasia, which is what the `is_installed`
    guard in `_lease_for` already exists for.
    """
    try:
        from toto.anastasia.executor.capsules import DEFAULT_INPUT_BUDGET
    except Exception:  # noqa: BLE001 — no manager here; keep the old ceiling
        return 64 * 1024 * 1024
    return DEFAULT_INPUT_BUDGET


MAX_HOME_RAW_BYTES = _staging_budget()

#: The stored blob's own ceiling. Equal to the raw budget rather than smaller:
#: gzip never expands by more than a hair, so a blob at this size implies a
#: raw total that already failed the check above.
MAX_HOME_BYTES = MAX_HOME_RAW_BYTES

#: Where a home is collected from and staged to, in the runner's own terms.
HOME_NAME = "home"


def _size_phrase(size: int) -> str:
    """A size in a unit that is not misleading at the boundary.

    "the home directory was 64 MB, above the 64 MB limit" is what integer MB
    produces for anything between 64.0 and 65.0 MB, and it reads as a bug
    rather than as a limit. The same failure `executor/staging.py` documents
    in `_budget_phrase`; re-implemented rather than imported because that lives
    in a package ambrosia does not depend on.
    """
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size // 1024} KB"
    return f"{size} bytes"


class HibernationError(Exception):
    """A refusal with a sentence for the person who asked."""


# --------------------------------------------------------------------------- #
# Blobs                                                                        #
# --------------------------------------------------------------------------- #

def pack(files: dict) -> bytes:
    """``{name: bytes}`` → one gzipped tar. Empty in, empty out."""
    if not files:
        return b""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in sorted(files):
            body = files[name]
            if not isinstance(body, bytes):
                continue
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def unpack(blob: bytes) -> dict:
    """The reverse. Never raises: a corrupt blob is nothing, not an exception.

    Tamper detection is the DIGEST's job and happens before this is reached —
    see :func:`rehydrate`. This only has to avoid turning a bad archive into a
    500 on a page somebody is trying to recover from.
    """
    if not blob:
        return {}
    out: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
            for member in archive:
                if not member.isfile():
                    continue
                # A tar can name ../ and absolute paths. Nothing here writes to
                # a filesystem, but the names are handed back to a caller that
                # stages them, so they are filtered at the door.
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    out[member.name] = handle.read()
    except (tarfile.TarError, OSError, EOFError):
        return {}
    return out


def digest_of(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest() if blob else ""


# --------------------------------------------------------------------------- #
# State                                                                        #
# --------------------------------------------------------------------------- #

def record_for(workspace):
    from .models import WorkspaceHibernation

    record, _created = WorkspaceHibernation.objects.get_or_create(
        workspace=workspace)
    return record


def is_hibernated(workspace) -> bool:
    from .models import WorkspaceHibernation

    return WorkspaceHibernation.objects.filter(
        workspace=workspace, hibernated_at__isnull=False).exists()


def _app_for(workspace):
    return registry.for_kind(workspace.kind)


def _lease_for(workspace):
    """The open lease this workspace's runtime is using, or None.

    IT HONOURS THE WORKSPACE'S PINNED GEAR. Until 2026-09-10 this filtered on
    the owner alone and took the OLDEST open lease, which is only ever right
    for an account holding exactly one. On a two-Capsule account, a workspace
    pinned to the second one had ``permanent_home`` read off the first — so a
    hibernate could decide not to keep a $HOME the user had paid to keep — and,
    because ``hibernate`` defaults to ``release_lease=True``, it then RELEASED
    that first Capsule, killing whatever was running in it. Nothing tested it.

    ``capsules.preferred`` is the same resolution the compile path already uses
    (``capsules.py:141``), and it validates the stored uuid against what the owner
    holds NOW, so a stale uuid from a released Capsule falls back to automatic
    rather than raising. Automatic keeps the historical oldest-lease behaviour,
    which is correct when the user never chose.
    """
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.anastasia"):
        return None
    from toto.anastasia.models import ComputeLease

    leases = ComputeLease.objects.open().filter(owner=workspace.owner)

    app = _app_for(workspace)
    if app is not None:
        from . import capsules

        try:
            pinned = capsules.preferred(workspace, app.namespace)
        except Exception:                                   # pragma: no cover
            pinned = None
        if pinned:
            chosen = leases.filter(uuid=pinned).first()
            if chosen is not None:
                return chosen

    return leases.order_by("created_at").first()


# --------------------------------------------------------------------------- #
# Down                                                                         #
# --------------------------------------------------------------------------- #

def hibernate(workspace, *, user=None, release_lease: bool = True) -> dict:
    """Stop everything this workspace is running and write down how to rebuild it.

    Idempotent: hibernating something already asleep returns its manifest
    rather than raising, because a double-click must not be an error.
    """
    record = record_for(workspace)
    if record.hibernated_at:
        return record.manifest or {}

    app = _app_for(workspace)
    lease = _lease_for(workspace)
    permanent_home = bool(getattr(lease, "permanent_home", False))

    # 1. Ask the lab what it wants kept, BEFORE anything is stopped — a home is
    #    read out of a live runtime's output area, and stopping destroys it.
    snapshot: dict = {}
    home_files: dict = {}
    if app is not None and app.snapshot is not None:
        try:
            snapshot = app.snapshot(workspace, permanent_home=permanent_home) or {}
        except Exception:  # noqa: BLE001 — a lab must not strand a workspace
            log.exception("ambrosia: snapshot failed for %s", workspace.slug)
            snapshot = {"error": "the workspace could not be fully captured"}
        home_files = snapshot.pop("home_files", None) or {}

    # 2. Stop the runtime. `teardown` is the hook that already exists for this.
    if app is not None:
        try:
            app.teardown(workspace)
        except Exception:  # noqa: BLE001
            log.exception("ambrosia: teardown failed for %s", workspace.slug)

    # 3. Give the capacity back. Unmounting would keep the booking, and the
    #    booking is what the pool counts.
    released = False
    if release_lease and lease is not None:
        from toto.anastasia import services as capsule_services

        try:
            capsule_services.release(lease=lease, reason="workspace hibernated",
                                  actor=user or workspace.owner)
            released = True
        except Exception:  # noqa: BLE001
            log.exception("ambrosia: could not release %s", lease.uuid)

    # THE RAW TOTAL FIRST, and before packing rather than after: it is the
    # number the restore path enforces, and gzipping 20 GB to discover it was
    # too big costs the whole compression for an answer already available.
    raw_bytes = sum(len(body) for body in home_files.values()
                    if isinstance(body, bytes))
    blob = b""
    if raw_bytes > MAX_HOME_RAW_BYTES:
        # Kept as a fact in the manifest rather than silently dropped: coming
        # back to a smaller home with no explanation is worse than being told.
        snapshot["home_skipped"] = (
            f"the home directory held {_size_phrase(raw_bytes)} of files, "
            f"above the {_size_phrase(MAX_HOME_RAW_BYTES)} that can be staged "
            "back into a Capsule. It was not kept — everything else was.")
    else:
        blob = pack(home_files)
        if len(blob) > MAX_HOME_BYTES:
            snapshot["home_skipped"] = (
                f"the packed home was {_size_phrase(len(blob))}, above the "
                f"{_size_phrase(MAX_HOME_BYTES)} limit. It was not kept — "
                "everything else was.")
            blob = b""

    manifest = {
        "kind": workspace.kind,
        "namespace": app.namespace if app else "",
        "depth": "hybrid" if (permanent_home and blob) else "manifest",
        "permanent_home": permanent_home,
        "lease_released": released,
        "hibernated_at": timezone.now().isoformat(),
        **snapshot,
    }

    with transaction.atomic():
        if blob:
            record.home.save(f"{workspace.slug}-home.tar.gz",
                             ContentFile(blob), save=False)
            record.home_digest = digest_of(blob)
            record.home_bytes = len(blob)
        record.manifest = manifest
        record.hibernated_at = timezone.now()
        record.rehydrated_at = None
        record.save()
    return manifest


# --------------------------------------------------------------------------- #
# Up                                                                           #
# --------------------------------------------------------------------------- #

def rehydrate(workspace, *, user=None, capsule_uuid=None) -> dict:
    """Bring a hibernated workspace back, or refuse and say why.

    The refusal that matters is capacity: hibernating gave the reservation back
    to a shared pool, and there may be nothing left to take. That is reported as
    a refusal rather than a failure — nothing has been lost, and trying again
    later will work.
    """
    record = record_for(workspace)
    if not record.hibernated_at:
        return record.manifest or {}

    manifest = record.manifest or {}
    home_files: dict = {}
    if record.home_digest:
        blob = _read_home(record)
        actual = digest_of(blob)
        if actual != record.home_digest:
            # Refuse rather than restore something that is not what was kept.
            # A home is executable state — shell profiles, a pip --user tree.
            raise HibernationError(
                _("this workspace's saved home does not match what was stored, "
                  "so it has not been restored. Nothing else was lost."))
        home_files = unpack(blob)

    app = _app_for(workspace)
    if app is not None and app.restore is not None:
        try:
            app.restore(workspace, manifest=manifest, home_files=home_files,
                        user=user, capsule_uuid=capsule_uuid)
        except Exception as exc:  # noqa: BLE001
            log.exception("ambrosia: restore failed for %s", workspace.slug)
            raise HibernationError(str(exc)) from exc

    record.hibernated_at = None
    record.rehydrated_at = timezone.now()
    record.save(update_fields=["hibernated_at", "rehydrated_at"])
    workspace.touch()
    return manifest


def _read_home(record) -> bytes:
    if not record.home:
        return b""
    try:
        with record.home.storage.open(record.home.name, "rb") as handle:
            return handle.read()
    except OSError:
        return b""


def staged_home(workspace) -> dict:
    """The kept home, as ``{"home/…": bytes}`` for a runtime to stage.

    Verified against its digest here too: this is reached on every start, not
    only on an explicit rehydration, and an unverified blob would be staged into
    a container that then reads it as configuration.
    """
    from .models import WorkspaceHibernation

    record = WorkspaceHibernation.objects.filter(workspace=workspace).first()
    if record is None or not record.home_digest:
        return {}
    blob = _read_home(record)
    if digest_of(blob) != record.home_digest:
        log.warning("ambrosia: home digest mismatch for %s — not staging",
                    workspace.slug)
        return {}
    return {f"{HOME_NAME}/{name}": body for name, body in unpack(blob).items()}
