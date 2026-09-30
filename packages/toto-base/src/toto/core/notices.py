"""Security notices mailed to a member about their own account (2026-09-30).

"Your password was changed", "a new sign-in" (``toto.core.user_sessions``),
"your e-mail was changed" (``toto.socialhub.email_change``): short mails that
tell the owner of an account something happened to it, so that a change they
did not make is noticed. Every one leaves through ``send_notice`` and nothing
else:

    send_notice(user, "password_changed", {"address": "203.0.113.7"})  -> bool

**One seam.** It is synchronous today, in the request that caused it. Stage 36
moves it onto Celery with retry; callers do not change, because they only
ever call this function and never look at a mail themselves.

**Fail-safe.** A notice is never worth failing the change it reports: the
password is already changed when it is sent. Anything that goes wrong — no
address on the account, a template that does not render, an SMTP server that
refuses — is logged and answered ``False``; nothing is raised into the view.

**Whatever backend there is.** Production still runs the console backend, so
today the mail lands in the log; the moment SMTP is armed the same call
delivers it. A notice carries no secret, no link that acts on the account and
no content of the member's — only what happened, when, and from which address.

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
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template import loader
from django.utils import timezone, translation

log = logging.getLogger("toto.core.notices")

#: The notices there are. A kind is its two templates' stem.
KINDS = frozenset({
    "password_changed",
    "new_sign_in",
    "email_change_confirm",
    "email_changed",
})

#: Carried to SMTP untouched; lets a mail log tell notices from other mail.
PURPOSE_HEADER = "X-Toto-Notice"


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
    """Mail ``user`` the notice ``kind``; True when the backend took it.

    ``to`` sends it to another address than the account's — only the e-mail
    change does that, see the module docstring. Never raises. See the module
    docstring for why and for what a template sees.
    """
    try:
        if kind not in KINDS:
            log.error("notice: unknown kind %r", kind)
            return False
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
        message = EmailMessage(
            subject=" ".join(subject.split()),
            body=body,
            to=[address],
            headers={PURPOSE_HEADER: kind},
        )
        sent = message.send(fail_silently=False)
        return bool(sent)
    except Exception as exc:  # noqa: BLE001 - a notice never fails what it reports
        # The class only: an SMTP error's text can carry the address.
        log.warning("notice: %s to account %s not sent (%s)", kind,
                    getattr(user, "pk", None), type(exc).__name__)
        return False
