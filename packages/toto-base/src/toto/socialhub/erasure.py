"""*Erase my account* on My account (2026-10-01, RODO art. 17).

The web FILES a request and nothing more. An erase is irreversible, and the
owner's rule stands: accounts are deleted at the console only (``erase_user``
in toto.core, reached over SSH by ``tools/delete_user.py`` and ``deploy.py
<config> erase-user``). So:

* :func:`file_request` — the member, on My account, after a confirmation
  saying who carries it out and what is kept. One open request at a time.
* :func:`decline` — a superuser on the Superuser plan, from the list
  (``socialhub:erasure_requests``), with a note the member reads.
* :func:`close_for_erasure` / :func:`record_done` — ``erase_user``, inside its
  transaction and after it: the member's open request is marked done as they
  are erased, and the chain says so. Nothing on the web marks one done.

On the chain: ``PRIVACY.ERASURE_REQUESTED`` (the member), ``_DECLINED`` (the
superuser), ``_DONE`` (the system, from the console).
"""

from __future__ import annotations

import shlex

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from toto.socialhub import audit
from toto.socialhub.models import ErasureRequest

#: What the list shows an operator to run; ``{username}`` is filled in,
#: shell-quoted. A host with its own console wrapper (zenobia's
#: ``tools/delete_user.py``) says so in ``SOCIALHUB_ERASURE_COMMAND``.
DEFAULT_COMMAND = "python manage.py erase_user {username}"


class Refused(Exception):
    """The request was not filed or changed; the message says why."""


def console_command(ticket) -> str:
    template = getattr(settings, "SOCIALHUB_ERASURE_COMMAND", "") or DEFAULT_COMMAND
    return template.format(username=shlex.quote(ticket.username))


def latest_for(user):
    return ErasureRequest.objects.filter(user=user).first()


def file_request(user, *, request=None) -> ErasureRequest:
    """File ``user``'s request to be erased. Raises :class:`Refused`."""
    if ErasureRequest.objects.filter(user=user, status=ErasureRequest.OPEN).exists():
        raise Refused(_("Your request to erase your account is already with the operators."))
    try:
        with transaction.atomic():
            ticket = ErasureRequest.objects.create(user=user, username=user.get_username())
    except IntegrityError:
        raise Refused(_("Your request to erase your account is already with the "
                        "operators.")) from None
    audit.erasure_requested(ticket, request=request)
    return ticket


def decline(ticket, *, by, note: str, request=None) -> ErasureRequest:
    """Decline an open request, saying why. Raises :class:`Refused`."""
    note = (note or "").strip()
    if not note:
        raise Refused(_("Say why it is declined: the member reads this note."))
    with transaction.atomic():
        ticket = ErasureRequest.objects.select_for_update().get(pk=ticket.pk)
        if not ticket.is_open:
            raise Refused(_("This request is no longer open."))
        ticket.status = ErasureRequest.DECLINED
        ticket.handled_by = by
        ticket.handled_at = timezone.now()
        ticket.note = note[:500]
        ticket.save(update_fields=["status", "handled_by", "handled_at", "note"])
    audit.erasure_declined(ticket, request=request)
    return ticket


def close_for_erasure(user) -> list[int]:
    """Mark ``user``'s open request done. Called by ``erase_user`` inside its
    transaction, before the account goes — while the request still points at
    it; if the erase fails, this rolls back with it."""
    pks = list(ErasureRequest.objects.filter(user=user, status=ErasureRequest.OPEN)
               .values_list("pk", flat=True))
    if pks:
        ErasureRequest.objects.filter(pk__in=pks).update(
            status=ErasureRequest.DONE, handled_at=timezone.now(), handled_by=None)
    return pks


def record_done(pks) -> None:
    """The chain's half of :func:`close_for_erasure`, once the erase is in."""
    for ticket in ErasureRequest.objects.filter(pk__in=list(pks)):
        audit.erasure_done(ticket)
