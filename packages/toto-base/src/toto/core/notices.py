"""Security notices mailed to a member about their own account (2026-09-30).

"Your password was changed", "a new sign-in" (``toto.core.user_sessions``),
"your e-mail was changed" (``toto.socialhub.email_change``): short mails that
tell the owner of an account something happened to it, so that a change they
did not make is noticed. Every one leaves through ``send_notice`` and nothing
else:

    send_notice(user, "password_changed", {"address": "203.0.113.7"})  -> bool

**One seam.** Callers only ever call this function and never look at a mail
themselves, so how it leaves is decided here alone.

**On the worker, with retry** (2026-10-01). Where a Celery worker runs
(``NOTICES_VIA_WORKER``; zenobia's deploy.py sets it where the stack runs
one) the notice is rendered here — in the member's language, with the time
of the event — and handed to ``toto.core.tasks.deliver_notice`` once the
current transaction COMMITS: the scheduled alert run mails from inside one,
and a change that rolls back must not have been announced. The task is one
of ``tasks.MAIL_TASKS``: a host may route it to a worker of its own, the only
one that holds the SMTP password (zenobia, 2026-10-02: the `mail` queue and
its ``celery_mail`` worker), so the worker that runs workflow code and parses
uploads never sees the login. The worker tries
``TRIES`` times, waiting ``RETRY_DELAYS`` between tries (five tries over about
an hour), then gives up. ``True`` then means "on its way", not "delivered". A
broker that cannot be reached is no reason to lose a notice: it is sent at
once instead. Without a worker (``manage.py runserver``, a profile with
``celery: false``, a host that sets nothing) it is sent at once, in the
process that asked, one try, as before — a queued mail would wait for a
worker that never comes.

**What is kept: the outcome, never the mail.** Each try's result is written
to ``NoticeDelivery``, one row per kind — sent, being retried, failed; how
many tries; the error's class and SMTP reply code; a keyed hash of the
recipient. No address in clear, no subject or body, no link or token, and
never the SMTP password, which only the processes that send are given
(toto.jess kept it in the database and was retired for that). It is not an outbox: a message that
failed is gone, and the monitoring's Mail check
(``toto.monit.record.check_mail``) says so when sends keep failing — a
backoff nobody can see would hide a broken mail server for hours.

The broker holds the rendered mail meanwhile (Redis, inside the stack; the
e-mail change's link with it). A task's return value and every log line name
the kind, the try and the error class — never the address, whose text an
SMTP error can carry.

**Fail-safe.** A notice is never worth failing the change it reports: the
password is already changed when it is sent. Anything that goes wrong — no
address on the account, a template that does not render, an SMTP server that
refuses (on the worker: refuses ``TRIES`` times) — is logged and recorded,
and answered ``False``; nothing is raised into the view.

**Whatever backend there is.** Production still runs the console backend, so
today the mail lands in the log; the moment SMTP is armed the same call
delivers it. A notice carries no secret, no link that acts on the account and
no content of the member's — only what happened, when, and from which address.

**In the bell too** (2026-10-04). Where ``toto.notify`` is installed, a new
sign-in and a changed password or e-mail address are also a notification in
the member's bell (``_bell``): the event alone, without the address or the
device the mail names.

The e-mail change is the exception, on purpose (2026-09-30): its two kinds
go elsewhere than ``user.email``, named by ``to=``. ``email_change_confirm``
carries the single-use link that proves the member reads the NEW address and
goes only to that address; ``email_changed`` goes to the OLD one, which the
account no longer names. Every other kind goes to the account's own address.

A kind is a pair of templates, ``core/notices/<kind>_subject.txt`` and
``core/notices/<kind>.txt``, listed in ``KINDS`` so that a misspelt kind is a
logged refusal rather than a template error in production. The templates see
the caller's context plus ``user``, ``site_name`` and ``when`` (now); they
render in the member's language and time zone.

**Operator alerts** leave through here too (2026-10-01): ``check_alert`` and
``check_recovered``, sent by ``toto.monit.alerts`` when a scheduled check goes
bad or comes back. They go to the addresses in ``ALERT_EMAILS``, not to an
account, so the caller passes ``user=None`` and ``to=``; the templates never
name a user, and render in the platform's language and time zone.
"""

from __future__ import annotations

import logging
import smtplib
from functools import partial

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.template import loader
from django.utils import timezone, translation
from django.utils.crypto import salted_hmac

log = logging.getLogger("toto.core.notices")

#: The notices there are. A kind is its two templates' stem.
KINDS = frozenset({
    "password_changed",
    "new_sign_in",
    "email_change_confirm",
    "email_changed",
    # To the operators, about the platform rather than an account.
    "check_alert",
    "check_recovered",
})

#: Carried to SMTP untouched; lets a mail log tell notices from other mail.
PURPOSE_HEADER = "X-Toto-Notice"

#: Seconds the worker waits before the 2nd, 3rd, 4th and 5th try: five tries,
#: the last about an hour after the first. A mail server down for longer than
#: that is the Mail check's to report, not a queue's to hide. No wait is longer
#: than half an hour: a retry waits on the worker unacknowledged, and Redis
#: hands a message unacknowledged past the broker's visibility timeout (35
#: minutes on zenobia) to a worker again — the mail would go twice.
RETRY_DELAYS = (2 * 60, 10 * 60, 20 * 60, 30 * 60)
TRIES = len(RETRY_DELAYS) + 1


class NotDelivered(Exception):
    """A try that failed, as the worker's retry carries it: the kind and the
    error's class only — what the log may show."""


def via_worker() -> bool:
    """Does a Celery worker take notices on this host? The host says so."""
    return bool(getattr(settings, "NOTICES_VIA_WORKER", False))


def recipient_hash(address: str) -> str:
    """The address as ``NoticeDelivery`` keeps it: an HMAC under SECRET_KEY,
    so the table alone cannot be matched against a list of addresses."""
    return salted_hmac("toto.core.notices.recipient", (address or "").strip().lower(),
                       algorithm="sha256").hexdigest()


def error_text(exc: BaseException) -> str:
    """What went wrong, fit to keep: the class, the SMTP reply code, a socket
    error's own words — never an SMTP server's text, which can echo the
    address or the login."""
    name = type(exc).__name__
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        try:
            codes = sorted({str(code) for code, _text in exc.recipients.values()})
        except Exception:  # noqa: BLE001 - a refusal shaped unlike smtplib's
            codes = []
        return f"{name} ({', '.join(codes)})" if codes else name
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"{name} ({exc.smtp_code})"
    if isinstance(exc, smtplib.SMTPException):
        return name
    if isinstance(exc, OSError) and exc.strerror:
        return f"{name}: {exc.strerror}"[:200]
    return name


def _site_name() -> str:
    try:
        from toto.core.models import Platform

        platform = Platform.objects.filter(active=True).only("site_name").first()
        if platform is not None and platform.site_name:
            return platform.site_name
    except Exception:  # noqa: BLE001 - a name is a nicety, never a reason not to send
        pass
    return getattr(settings, "SITE_NAME", "") or ""


def _member_zone(user):
    """The member's chosen zone, or None for the platform's."""
    zone = getattr(getattr(user, "community_profile", None), "timezone", "") or ""
    if zone:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(zone)
        except Exception:  # noqa: BLE001 - a stale zone name falls back to the platform's
            pass
    return None


def _language(user) -> str:
    person = getattr(user, "community_profile", None)
    return getattr(person, "preferred_language", "") or settings.LANGUAGE_CODE


def send_notice(user, kind: str, context: dict | None = None, *, to: str = "") -> bool:
    """Mail ``user`` the notice ``kind``; True when it is on its way.

    On its way: queued for the worker (after the current transaction
    commits), or — without one — taken by the backend here and now. ``to``
    sends it to another address than the account's — only the e-mail change
    does that, see the module docstring. Never raises. See the module
    docstring for why and for what a template sees.
    """
    address = ""
    try:
        if kind not in KINDS:
            log.error("notice: unknown kind %r", kind)
            return False
        # In the bell too (2026-10-04), before the address is looked at: an
        # account with no e-mail address gets no mail and still hears of it.
        _bell(user, kind)
        address = (to or getattr(user, "email", "") or "").strip()
        if not address:
            log.info("notice: %s not sent — account %s has no e-mail address",
                     kind, getattr(user, "pk", None))
            return False
        values = {"user": user, "site_name": _site_name(), "when": timezone.now()}
        values.update(context or {})
        # In the member's language and time zone, whatever the request that
        # caused it spoke — a template's dates follow the active zone.
        with translation.override(_language(user)), timezone.override(_member_zone(user)):
            subject = loader.render_to_string(f"core/notices/{kind}_subject.txt", values)
            body = loader.render_to_string(f"core/notices/{kind}.txt", values)
        message = {"kind": kind, "to": address, "subject": " ".join(subject.split()),
                   "body": body}
    except Exception as exc:  # noqa: BLE001 - a notice never fails what it reports
        from .models import NoticeDelivery

        # The class only: an SMTP error's text can carry the address.
        log.warning("notice: %s to account %s not sent (%s)", kind,
                    getattr(user, "pk", None), type(exc).__name__)
        # A template that does not render is a send that failed, as much as
        # a server that refuses — the Mail check counts it.
        record(kind, address, NoticeDelivery.FAILED, error=error_text(exc))
        return False
    if via_worker():
        try:
            # Nothing leaves for a change that is rolled back; outside a
            # transaction (a view, in autocommit) this runs at once.
            transaction.on_commit(partial(_queue, message))
            return True
        except Exception as exc:  # noqa: BLE001 - manual transaction management
            log.warning("notice: %s could not wait for the commit (%s); sent at once",
                        kind, type(exc).__name__)
    return not deliver(message)


def _bell(user, kind: str) -> None:
    """The same event as a notification in the member's bell, where
    ``toto.notify`` is installed: a new sign-in, a changed password or e-mail
    address (``toto.notify.kinds.NOTICE_KINDS`` — never the confirmation
    link, never an operators' alert). It names the event and nothing else:
    no address, no device. Never raises."""
    if user is None or not getattr(user, "pk", None):
        return
    try:
        from django.apps import apps

        if not apps.is_installed("toto.notify"):
            return
        from toto.notify.sources import account_notice

        account_notice(user, kind)
    except Exception as exc:  # noqa: BLE001 - a notice never fails what it reports
        log.warning("notice: %s not put in the bell (%s)", kind, type(exc).__name__)


def _queue(message: dict) -> None:
    """Hand a rendered notice to the worker; send it here if that fails."""
    try:
        from .tasks import deliver_notice

        deliver_notice.delay(message)
    except Exception as exc:  # noqa: BLE001 - no broker is no reason to lose it
        log.warning("notice: %s could not be queued (%s); sent at once instead",
                    message.get("kind"), type(exc).__name__)
        deliver(message)


def deliver(message: dict, *, tries: int = 1, final: bool = True) -> str:
    """Send one rendered notice and record how it went: "" when the backend
    took it, else what went wrong (``error_text``). Never raises.

    ``tries`` is this try's number; ``final`` says no other follows, so a
    failure is the send's end rather than a "being retried".
    """
    from .models import NoticeDelivery

    kind, address = str(message.get("kind", "")), str(message.get("to", ""))
    try:
        sent = EmailMessage(subject=message.get("subject", ""), body=message.get("body", ""),
                            to=[address], headers={PURPOSE_HEADER: kind}
                            ).send(fail_silently=False)
        error = "" if sent else "NotSent"
    except Exception as exc:  # noqa: BLE001 - reported below, never raised
        error = error_text(exc)
    if not error:
        record(kind, address, NoticeDelivery.SENT, tries=tries)
        return ""
    record(kind, address, NoticeDelivery.FAILED if final else NoticeDelivery.RETRYING,
           tries=tries, error=error)
    log.warning("notice: %s not sent, try %d of %s (%s)%s", kind, tries,
                tries if final else TRIES, error,
                "" if final else "; it is tried again")
    return error


def record(kind: str, address: str, status: str, *, tries: int = 1, error: str = "") -> None:
    """Keep how a send of ``kind`` went, on its ``NoticeDelivery`` row.

    Never raises: a mail that left is not undone by a record that could not
    be written (a table not migrated yet, a database gone).
    """
    try:
        from .models import NoticeDelivery

        now = timezone.now()
        with transaction.atomic():
            if status == NoticeDelivery.SENT:
                # A delivery proves the way out works: the failures counted
                # so far, of every kind, are behind it. Before this kind's
                # own row is locked, so that two deliveries at once take
                # the rows in one order and cannot deadlock.
                NoticeDelivery.objects.filter(failures__gt=0).update(failures=0)
            row, _created = (NoticeDelivery.objects.select_for_update()
                             .get_or_create(purpose=kind[:40]))
            row.recipient_hash = recipient_hash(address) if address else ""
            row.status, row.tries, row.error, row.updated_at = status, tries, error[:200], now
            if status == NoticeDelivery.SENT:
                row.sent_at, row.failures = now, 0
            elif status == NoticeDelivery.FAILED:
                row.failures += 1
            row.save()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("notice: the outcome of %s was not recorded (%s)", kind,
                    type(exc).__name__)
