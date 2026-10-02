"""System checks on the platform's own settings.

**REQUIRE_SMTP** (2026-10-01). A host whose deployment must send real mail —
password resets, the security notices a member gets, the operators' alerts —
sets it, and from then on a process refuses to start while its mail settings
could not send any: ``manage.py check`` reports Errors, and so does every
management command that runs the checks, a container's start among them.
What counts as "could not send": a backend that keeps the mail on the machine
(console, file, locmem, dummy — anything that is not Django's SMTP backend or
a subclass of it), no mail server (empty, or Django's own ``localhost``), no
TLS (neither STARTTLS nor implicit TLS), Django's placeholder senders
(``webmaster@localhost``, ``root@localhost``, or any address at localhost),
and no timeout, which would let a mail server that stops answering hold a
request or a worker for good.

Off — the default — nothing here is looked at: a laptop's console backend is
a choice, not a fault.

The password is not looked at. A host keeps it where it decides (zenobia,
2026-10-02: smtp.yaml beside the deploy profiles, copied at deploy into an
env file only web and the mail worker read), and only a login against the
real server can prove it; zenobia's deploy does that (``deploy.py <config>
up`` on a cloud profile, ``manage.py mail_check``).
zenobia's deploy.py writes ``REQUIRE_SMTP=1`` for a profile that says
``deployment.environment: cloud``.
"""

from __future__ import annotations

from email.utils import parseaddr

from django.conf import settings
from django.core.checks import Error, register

#: Django's SMTP backend. A host's own backend that subclasses it counts.
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

#: Names that are this machine, for a mail server and for a sender's domain.
_THIS_MACHINE = {"", "localhost", "127.0.0.1", "::1", "localhost.localdomain"}


def _sends_over_smtp(path: str) -> bool:
    from django.core.mail.backends.smtp import EmailBackend
    from django.utils.module_loading import import_string

    try:
        backend = import_string(path)
    except ImportError:
        return False
    return isinstance(backend, type) and issubclass(backend, EmailBackend)


def _sender_domain(address) -> str:
    return parseaddr(str(address or ""))[1].rpartition("@")[2].strip().lower()


@register()
def check_required_smtp(app_configs, **kwargs):
    if not getattr(settings, "REQUIRE_SMTP", False):
        return []
    errors = []

    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "")
    if not _sends_over_smtp(backend):
        errors.append(Error(
            f"EMAIL_BACKEND is {backend!r}, which sends no mail over SMTP, "
            "and REQUIRE_SMTP says this deployment must",
            hint="Configure the mail server (zenobia: smtp.yaml beside the "
                 "deploy profiles). The console, file, locmem and dummy backends keep "
                 "every mail on this machine.",
            id="core.E001"))

    host = str(getattr(settings, "EMAIL_HOST", "") or "").strip()
    if host.lower() in _THIS_MACHINE:
        errors.append(Error(
            f"EMAIL_HOST is {host!r}: no mail server is named",
            hint="Name the SMTP server. Django's default, localhost, is this "
                 "machine — inside a container, the container itself.",
            id="core.E002"))

    if not (getattr(settings, "EMAIL_USE_TLS", False)
            or getattr(settings, "EMAIL_USE_SSL", False)):
        errors.append(Error(
            "neither EMAIL_USE_TLS (STARTTLS) nor EMAIL_USE_SSL (implicit "
            "TLS) is on: the login and every mail would cross the network in "
            "the clear",
            hint="STARTTLS on port 587 or implicit TLS on port 465.",
            id="core.E003"))

    for name, code in (("DEFAULT_FROM_EMAIL", "core.E004"),
                       ("SERVER_EMAIL", "core.E005")):
        address = getattr(settings, name, "")
        if _sender_domain(address) in _THIS_MACHINE:
            errors.append(Error(
                f"{name} is {address!r}, an address at this machine that no "
                "mail server will relay or anybody answer",
                hint="A sender on the platform's own domain (zenobia: "
                     "smtp.yaml's from).",
                id=code))

    timeout = getattr(settings, "EMAIL_TIMEOUT", None)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) \
            or timeout <= 0:
        errors.append(Error(
            f"EMAIL_TIMEOUT is {timeout!r}: a mail server that stops "
            "answering would hold a request or a worker for good",
            hint="A number of seconds (zenobia: smtp.yaml's timeout, 15 by "
                 "default).",
            id="core.E006"))
    return errors
