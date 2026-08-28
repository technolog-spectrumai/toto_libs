"""Is the record intact, and is it backed up — measured on request.

The other half of monit. The collectors and their history answer questions
about the MACHINE: CPU, memory, disk pressure, service liveness, as trends.
This module answers questions about the PLATFORM's own record, none of which
are trends: are there unapplied migrations, does the audit chain still
verify, when was the last backup taken and is it recent enough to be worth
having, can the media store be written to. Every check runs when the page is
requested, and none is graphed — a broken audit chain is not interesting as
a chart.

Ported from the placidia truth book's ops app, generalised: the paths it
probes come from settings every host has (`MEDIA_ROOT`) or declares
(`MONIT_BACKUP_DIRS`), and the placidia-only checks (its PDF renderer, its
read API) stayed home.

**The one rule, inherited verbatim: a failing check returns UNKNOWN, never
raises.** A status page that 500s because one probe threw is a status page
that is unavailable exactly when it is needed.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

OK = "ok"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"
OFF = "off"

#: A backup older than this is worth saying out loud. Two days rather than
#: one: a nightly schedule plus a slow run must not cry wolf every morning.
BACKUP_STALE_HOURS = 48
#: Below this, the disk is the next outage.
DISK_WARN_PERCENT = 85
DISK_FAIL_PERCENT = 95


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    status: str
    summary: str
    detail: str = ""
    value: str = ""

    @property
    def is_bad(self):
        return self.status in (WARN, FAIL)


def _bytes(n):
    if n is None:
        return "—"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < step:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} PB"


def _age(seconds):
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.0f} h ago"
    return f"{hours / 24:.0f} days ago"


def _guard(key, label):
    """Wrap a collector so a broken probe reports UNKNOWN instead of 500ing."""
    def decorate(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - the whole point
                return Check(key, label, UNKNOWN, "Could not be checked.",
                             detail=f"{exc.__class__.__name__}: {exc}"[:300])
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return decorate


def _writable(path: Path) -> bool:
    probe = path / ".monit-write-probe"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


@_guard("database", "Database")
def check_database():
    from django.db import connection

    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    latency = (time.perf_counter() - started) * 1000

    status = OK if latency < 250 else WARN
    return Check("database", "Database", status,
                 f"Reachable, {latency:.1f} ms.",
                 detail=f"{connection.vendor} · "
                        f"{connection.settings_dict.get('NAME')}",
                 value=f"{latency:.1f} ms")


@_guard("migrations", "Migrations")
def check_migrations():
    """Unapplied migrations mean the code and the schema disagree."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if not plan:
        return Check("migrations", "Migrations", OK, "All applied.",
                     value=str(len(executor.loader.applied_migrations)))
    names = ", ".join(f"{m.app_label}.{m.name}" for m, _ in plan[:5])
    return Check("migrations", "Migrations", FAIL,
                 f"{len(plan)} not applied.", detail=names,
                 value=str(len(plan)))


@_guard("media", "Media store")
def check_media():
    """Uploads, logos, documents: the files the rows point at."""
    from django.conf import settings

    root = Path(settings.MEDIA_ROOT)
    if not root.exists():
        return Check("media", "Media store", FAIL,
                     "The directory does not exist.", detail=str(root))
    if not _writable(root):
        return Check("media", "Media store", FAIL,
                     "Not writable by this process.", detail=str(root))

    files = [p for p in root.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    return Check("media", "Media store", OK,
                 f"{len(files)} file(s), {_bytes(total)}.",
                 detail=str(root), value=str(len(files)))


@_guard("disk", "Disk")
def check_disk():
    from django.conf import settings

    root = Path(settings.MEDIA_ROOT)
    probe = root if root.exists() else Path(settings.BASE_DIR)
    usage = shutil.disk_usage(probe)
    used_percent = usage.used / usage.total * 100 if usage.total else 0

    status = OK
    if used_percent >= DISK_FAIL_PERCENT:
        status = FAIL
    elif used_percent >= DISK_WARN_PERCENT:
        status = WARN
    return Check("disk", "Disk", status,
                 f"{used_percent:.0f}% used, {_bytes(usage.free)} free.",
                 detail=str(probe), value=f"{used_percent:.0f}%")


@_guard("backups", "Backups")
def check_backups():
    """Freshness, not existence. A backup directory full of last year is
    worse than an empty one, because it looks like a working backup.

    The host names its directories in ``MONIT_BACKUP_DIRS``; deploy.py's
    sidecars write per-stack directories the settings file knows. Unset means
    the host takes its backups some other way — reported as OFF, not judged.
    """
    from django.conf import settings

    roots = [Path(p) for p in getattr(settings, "MONIT_BACKUP_DIRS", [])]
    if not roots:
        return Check("backups", "Backups", OFF,
                     "No backup directories declared.",
                     detail="Set MONIT_BACKUP_DIRS to watch them here.")

    newest, label = None, ""
    missing = [str(root) for root in roots if not root.exists()]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            stamp = path.stat().st_mtime
            if newest is None or stamp > newest:
                newest, label = stamp, path.name

    if newest is None:
        return Check("backups", "Backups", WARN,
                     "No backup has been taken.",
                     detail=", ".join(str(r) for r in roots))

    age = time.time() - newest
    status = OK if age < BACKUP_STALE_HOURS * 3600 else FAIL
    detail = label + (f" · missing: {', '.join(missing)}" if missing else "")
    return Check("backups", "Backups", status,
                 f"Newest {_age(age)}.", detail=detail, value=_age(age))


@_guard("audit", "Audit chain")
def check_audit():
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.audit"):
        return Check("audit", "Audit chain", OFF,
                     "toto.audit is not installed on this host.")
    from toto.audit.services import verify_chain

    result = verify_chain()
    if result.ok:
        return Check("audit", "Audit chain", OK,
                     f"{result.checked} record(s) verify.",
                     value=str(result.checked))
    return Check("audit", "Audit chain", FAIL,
                 f"Broken at sequence {result.first_bad_sequence}.",
                 detail=result.detail, value="broken")


ALL_CHECKS = (check_database, check_migrations, check_media, check_disk,
              check_backups, check_audit)


def run_checks():
    return [check() for check in ALL_CHECKS]


def worst(checks) -> str:
    for level in (FAIL, WARN, UNKNOWN):
        if any(check.status == level for check in checks):
            return level
    return OK
