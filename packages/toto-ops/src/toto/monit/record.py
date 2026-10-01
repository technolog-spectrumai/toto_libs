"""Is the record intact, and is it backed up — measured on request.

The other half of monit. The collectors and their history answer questions
about the MACHINE: CPU, memory, disk pressure, service liveness, as trends.
This module answers questions about the PLATFORM's own record, none of which
are trends: are there unapplied migrations, does the audit chain still
verify, when was the last backup taken and is it recent enough to be worth
having, can the media store be written to, how long has the certificate got.
Every check runs when the page is requested, and none is graphed — a broken
audit chain is not interesting as a chart. Since 2026-10-01 the same checks
also run on the beat, and a change is mailed (``toto.monit.alerts``); one of
them asks whether the beat itself keeps time (``check_overdue``), and one
whether that mail still leaves (``check_mail``). On the beat, the two that
read everything take a cheaper form (``SCHEDULED_FORMS``): the media store
is not counted file by file, and the audit chain is walked whole once a day
and otherwise from where the last walk ended.

Ported from the placidia truth book's ops app, generalised: the paths it
probes come from settings every host has (`MEDIA_ROOT`) or declares
(`MONIT_BACKUP_DIRS`), and the placidia-only checks (its PDF renderer, its
read API) stayed home.

**The one rule, inherited verbatim: a failing check returns UNKNOWN, never
raises.** A status page that 500s because one probe threw is a status page
that is unavailable exactly when it is needed.
"""

from __future__ import annotations

import ipaddress
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

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
#: The platform's own certificate (2026-10-01). Three weeks left is a renewal
#: that has stopped happening, found with time to mend it; one week left is an
#: outage already booked. Let's Encrypt renews at thirty days, so a working
#: renewal never comes near either line.
CERT_WARN_DAYS = 21
CERT_FAIL_DAYS = 7
#: The handshake runs inside the Database page's request too; it must not
#: hold the page for long when the name does not answer.
CERT_TIMEOUT_SECONDS = 5
#: The Mail check (2026-10-01) warns when this many notices in a row, of any
#: kind and each after all its tries, could not be delivered: one is a
#: refused address, several are the mail server or its password.
MAIL_FAILURES_WARN = 3
#: Names no certificate authority will vouch for: a local profile's, a lab's.
PRIVATE_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".lan",
                    ".home.arpa", ".test", ".example", ".invalid")
#: The scheduled run (``run_checks(scheduled=True)``, every few minutes) walks
#: the whole audit chain at most this often; in between it walks only what was
#: appended since its last walk, from the hash that walk ended on (2026-10-01,
#: the review). The page walks the whole chain whenever it opens.
AUDIT_WALK_HOURS = 24
#: Where the scheduled run keeps how far it has verified: a cache entry, so a
#: cache that forgets costs a whole walk and nothing else.
AUDIT_MARK_KEY = "toto:monit:audit-verified"


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
def check_media(*, scheduled=False):
    """Uploads, logos, documents: the files the rows point at.

    ``scheduled`` (the alert run, 2026-10-01): there and writable — all an
    alert is about — without counting every file, which walks the whole
    tree; the page counts them.
    """
    from django.conf import settings

    root = Path(settings.MEDIA_ROOT)
    if not root.exists():
        return Check("media", "Media store", FAIL,
                     "The directory does not exist.", detail=str(root))
    if not _writable(root):
        return Check("media", "Media store", FAIL,
                     "Not writable by this process.", detail=str(root))
    if scheduled:
        return Check("media", "Media store", OK, _("Writable."), detail=str(root))

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


def _verify_audit_on_schedule():
    """The scheduled form of the audit check: the whole chain once every
    AUDIT_WALK_HOURS, and in between what was appended since the last walk,
    from the hash it ended on (``verify_chain(after=)``) — each run's cost
    is the new records, not the chain. How far it got is kept in the cache
    (AUDIT_MARK_KEY); nothing kept, or another chain, is a whole walk."""
    from django.core.cache import cache

    from toto.audit.models import AuditChain, chain_key
    from toto.audit.services import verify_chain

    chain = AuditChain.objects.filter(key=chain_key()).first()
    if chain is None:
        return verify_chain()
    try:
        mark = cache.get(AUDIT_MARK_KEY) or {}
    except Exception:  # noqa: BLE001 - a cache that cannot answer is a whole walk
        mark = {}
    now = time.time()
    whole = (mark.get("chain") != chain.pk or not mark.get("sequence")
             or now - float(mark.get("walked_at") or 0) >= AUDIT_WALK_HOURS * 3600)
    if whole:
        result, walked_at = verify_chain(chain), now
    else:
        result = verify_chain(chain, after=(mark["sequence"], mark["hash"]))
        walked_at = mark["walked_at"]
    if result.ok and result.checked:
        last = (chain.records.filter(sequence=result.checked)
                .values_list("record_hash", flat=True).first())
        if last:
            try:
                cache.set(AUDIT_MARK_KEY, {"chain": chain.pk, "sequence": result.checked,
                                           "hash": last, "walked_at": walked_at},
                          timeout=2 * AUDIT_WALK_HOURS * 3600)
            except Exception:  # noqa: BLE001 - the next run walks it all again
                pass
    return result


@_guard("audit", "Audit chain")
def check_audit(*, scheduled=False):
    """Does the audit chain still verify? ``scheduled`` (the alert run,
    2026-10-01): the new records every run and the whole chain daily
    (``_verify_audit_on_schedule``); the page walks it all."""
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.audit"):
        return Check("audit", "Audit chain", OFF,
                     "toto.audit is not installed on this host.")
    from toto.audit.services import verify_chain

    result = _verify_audit_on_schedule() if scheduled else verify_chain()
    if result.ok:
        return Check("audit", "Audit chain", OK,
                     f"{result.checked} record(s) verify.",
                     value=str(result.checked))
    return Check("audit", "Audit chain", FAIL,
                 f"Broken at sequence {result.first_bad_sequence}.",
                 detail=result.detail, value="broken")


def _is_public_name(host: str) -> bool:
    """A name a public certificate can be issued for — not localhost, not an
    address, not a name only a local network resolves."""
    if not host or host == "localhost" or "." not in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not host.endswith(PRIVATE_SUFFIXES)


def cert_target():
    """``(host, port)`` whose certificate the check reads, or None.

    ``MONIT_CERT_DOMAIN`` when the host defines it — zenobia's deploy.py writes
    the profile's ``ssl.domain`` there, and an EMPTY value where nginx serves
    no certificate (``ssl.mode: none``), which means "not checked" — else
    ``PLATFORM_DOMAIN``. A scheme and a path are ignored, a port is kept, and
    an explicit ``http://`` means the platform is served in the clear: there is
    no certificate to read.
    """
    from django.conf import settings

    raw = getattr(settings, "MONIT_CERT_DOMAIN", None)
    if raw is None:
        raw = getattr(settings, "PLATFORM_DOMAIN", "")
    raw = str(raw or "").strip()
    if raw.lower().startswith("http://"):
        return None
    raw = raw.split("://", 1)[-1].split("/", 1)[0]
    host, port = raw, 443
    if raw.count(":") == 1 and raw.rsplit(":", 1)[1].isdigit():
        host, port = raw.rsplit(":", 1)[0], int(raw.rsplit(":", 1)[1])
    host = host.strip("[]").rstrip(".").lower()
    return (host, port) if _is_public_name(host) else None


def peer_certificate(host: str, port: int) -> bytes:
    """The DER certificate ``host:port`` presents for ``host``.

    UNVERIFIED, on purpose: the question is when it expires, and a self-signed
    certificate (``ssl.mode: gervazy``) or an expired one has to be read too —
    a verifying handshake refuses exactly the certificate this check is for.
    Nothing is sent over the connection.
    """
    import socket
    import ssl

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=CERT_TIMEOUT_SECONDS) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise ValueError("the server presented no certificate")
    return der


def not_after(der: bytes):
    """A DER certificate's notAfter, as an aware UTC datetime."""
    from datetime import timezone as dt_timezone

    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    expires = getattr(cert, "not_valid_after_utc", None)
    if expires is None:                    # cryptography older than 42
        expires = cert.not_valid_after.replace(tzinfo=dt_timezone.utc)
    return expires


@_guard("certificate", gettext_lazy("Certificate"))
def check_certificate():
    """How long the platform's own certificate has left, read in a TLS
    handshake with its public name (2026-10-01).

    A local profile has no public name — localhost, an address — and is
    reported OFF, "not checked", rather than judged. A name that does not
    answer is UNKNOWN through the guard, with the reason in the detail.
    """
    from django.utils import timezone

    label = _("Certificate")
    target = cert_target()
    if target is None:
        return Check("certificate", label, OFF,
                     _("Not checked: this platform has no public domain."),
                     detail=_("Set PLATFORM_DOMAIN (or the profile's ssl.domain) "
                              "to a public name to check its certificate."))
    try:
        import cryptography  # noqa: F401 - lazily, like every third party here
    except ImportError:
        return Check("certificate", label, OFF,
                     _("Not checked: the cryptography package is not installed."))

    host, port = target
    expires = not_after(peer_certificate(host, port))
    left = (expires - timezone.now()).total_seconds()
    days = int(left // 86400)
    on = expires.strftime("%Y-%m-%d")
    if left <= 0:
        summary = _("Expired on %(date)s.") % {"date": on}
    else:
        summary = ngettext("Expires on %(date)s, in %(days)d day.",
                           "Expires on %(date)s, in %(days)d days.",
                           days) % {"date": on, "days": days}
    status = OK
    if left < CERT_FAIL_DAYS * 86400:
        status = FAIL
    elif left < CERT_WARN_DAYS * 86400:
        status = WARN
    return Check("certificate", label, status, summary,
                 detail=host if port == 443 else f"{host}:{port}",
                 value=str(max(days, 0)))


@_guard("overdue", gettext_lazy("Scheduled tasks"))
def check_overdue():
    """Has every beat entry started as often as its schedule says
    (2026-10-01)?

    Each entry's newest run against its cadence (``toto.monit.heartbeats``):
    not started within twice the cadence plus a grace is WARN, three times
    FAIL. When nothing scheduled has started in the most frequent entry's
    window the summary says what that is — beat, or every worker, gone —
    rather than listing every entry.
    """
    from . import heartbeats

    label = _("Scheduled tasks")
    states = heartbeats.entries()
    judged = [state for state in states if state.status != OFF]
    if not judged:
        return Check("overdue", label, OFF,
                     _("Nothing is scheduled on this host.") if not states else
                     _("No schedule here has a cadence that can be judged."))
    late = [state for state in judged if state.late]
    status = OK
    if any(state.status == FAIL for state in late):
        status = FAIL
    elif late:
        status = WARN
    quiet = heartbeats.quiet_for(judged)
    names = ", ".join(state.name for state in late[:5]) + (", …" if len(late) > 5 else "")
    if quiet is not None and len(late) == len(judged):
        summary = _("Every scheduled task is overdue: celery beat is not running, "
                    "or no worker takes its tasks.")
    elif quiet is not None:
        summary = _("Nothing scheduled has started for %(quiet)s: celery beat is "
                    "not running, or no worker takes its tasks.") % {
                        "quiet": heartbeats.span(quiet)}
    elif late:
        summary = ngettext(
            "%(late)d of %(count)d scheduled tasks is overdue: %(names)s.",
            "%(late)d of %(count)d scheduled tasks are overdue: %(names)s.",
            len(late)) % {"late": len(late), "count": len(judged), "names": names}
    else:
        summary = ngettext("%(count)d scheduled task, none overdue.",
                           "%(count)d scheduled tasks, none overdue.",
                           len(judged)) % {"count": len(judged)}
    detail = "; ".join(heartbeats.lateness(state) for state in late)
    if quiet is not None:
        from django.utils import timezone

        started = heartbeats.beat_started()
        said = (_("Beat last started %(when)s.") % {
                    "when": timezone.localtime(started).strftime("%Y-%m-%d %H:%M")}
                if started else _("No start of celery beat has been recorded."))
        detail = f"{said} {detail}"
    return Check("overdue", label, status, summary, detail=detail,
                 value=str(len(late)))


def backend_name() -> str:
    """The mail backend's short name: "django.core.mail.backends.console.
    EmailBackend" is "console" — what the Mail check and the alert summary
    say when nothing reaches anyone."""
    from django.conf import settings

    parts = str(getattr(settings, "EMAIL_BACKEND", "")).split(".")
    return parts[-2] if len(parts) > 1 else parts[0]


@_guard("mail", gettext_lazy("Mail"))
def check_mail():
    """Do the notices and the alert mail still leave (2026-10-01)?

    Read off ``toto.core.models.NoticeDelivery``: the last outcome of each
    notice kind, and how many sends in a row failed after every try
    (``toto.core.notices``, on the worker five tries over about an hour).
    WARN at ``MAIL_FAILURES_WARN``; a delivery of any kind makes it OK
    again. A backend that delivers nothing (console, dummy) succeeds every
    time and reaches nobody, so the detail says it.
    """
    from django.utils import timezone

    from toto.core.email_config import email_delivery_configured
    from toto.core.models import NoticeDelivery

    label = _("Mail")
    rows = list(NoticeDelivery.objects.all())
    failures = sum(row.failures for row in rows)
    troubled = sorted((row for row in rows if row.status != NoticeDelivery.SENT),
                      key=lambda row: row.updated_at, reverse=True)
    notes = []
    if not email_delivery_configured():
        notes.append(_("Nothing reaches anyone: the mail backend is %(backend)s.")
                     % {"backend": backend_name()})
    notes += [_("%(kind)s: %(status)s, try %(tries)d, %(error)s, %(when)s") % {
                  "kind": row.purpose, "status": row.get_status_display(),
                  "tries": row.tries, "error": row.error or "—",
                  "when": timezone.localtime(row.updated_at).strftime("%Y-%m-%d %H:%M")}
              for row in troubled]
    detail = "; ".join(str(note) for note in notes)

    if failures >= MAIL_FAILURES_WARN:
        failed = [row for row in troubled if row.status == NoticeDelivery.FAILED]
        summary = ngettext("The last %(count)d notice could not be delivered.",
                           "The last %(count)d notices could not be delivered.",
                           failures) % {"count": failures}
        if failed and failed[0].error:
            summary += " " + _("Last error: %(error)s.") % {"error": failed[0].error}
        return Check("mail", label, WARN, summary, detail=detail, value=str(failures))
    sent = [row.sent_at for row in rows if row.sent_at]
    if failures:
        summary = ngettext(
            "%(count)d notice in a row could not be delivered (a warning at %(limit)d).",
            "%(count)d notices in a row could not be delivered (a warning at %(limit)d).",
            failures) % {"count": failures, "limit": MAIL_FAILURES_WARN}
    elif sent:
        summary = _("The last notice was sent %(age)s.") % {
            "age": _age((timezone.now() - max(sent)).total_seconds())}
    elif rows:
        summary = _("No notice has been delivered yet.")
    else:
        summary = _("No notice has been sent yet.")
    return Check("mail", label, OK, summary, detail=detail, value=str(failures))


ALL_CHECKS = (check_database, check_migrations, check_media, check_disk,
              check_backups, check_audit, check_certificate, check_overdue,
              check_mail)


#: The checks with a cheaper form for the scheduled run (2026-10-01): the two
#: that read everything — every media file, every audit record — and would do
#: it every ALERT_CHECK_MINUTES.
SCHEDULED_FORMS = (check_media, check_audit)


def run_checks(*, scheduled=False):
    """Every check, as the page shows them; ``scheduled`` (the alert run)
    takes each check's cheaper form where it has one (SCHEDULED_FORMS)."""
    return [check(scheduled=True) if scheduled and check in SCHEDULED_FORMS else check()
            for check in ALL_CHECKS]


def worst(checks) -> str:
    for level in (FAIL, WARN, UNKNOWN):
        if any(check.status == level for check in checks):
            return level
    return OK
