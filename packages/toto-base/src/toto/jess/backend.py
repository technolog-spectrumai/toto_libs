"""``JessEmailBackend`` — a Django email backend that queues instead of sending.

Point ``EMAIL_BACKEND`` at this and every existing sender in the platform becomes
asynchronous and visible, with no caller changes: Django's own
``PasswordResetForm.save()`` calls ``send_mail()``, which resolves this backend, which
writes a row and hands it to Celery.

**Why the queue boundary is here and not in Jess's own views.** An SMTP conversation is a
network call to somebody else's server. Doing it inside a web request holds a worker for
as long as the far end feels like taking, which is exactly what the platform's
``statement_timeout`` ladder exists to prevent. Putting the boundary in the backend means
there is no way for a caller to accidentally bypass it.

**Two semantic changes this makes, both deliberate:**

1. ``send_messages()`` returns the number of messages ACCEPTED, not delivered. Django's
   contract says "number of messages sent successfully", and honouring that literally
   would mean blocking. Callers in this codebase use it as a truthiness check at most.
2. ``django.core.mail.outbox`` no longer fills, because nothing is handed to locmem. The
   existing password-reset tests are unaffected only because they
   ``override_settings(EMAIL_BACKEND=locmem)`` and so never resolve this class —
   ``jess/tests.py`` asserts that reasoning rather than trusting it.
"""
from __future__ import annotations

import logging

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

# Recognised on an EmailMessage to tag the outbox row, e.g.
#   msg.extra_headers["X-Jess-Purpose"] = "password_reset"
# A header rather than a subclass so a caller does not have to import Jess to label its
# mail — and it is stripped before sending so it never reaches the wire.
PURPOSE_HEADER = "X-Jess-Purpose"


class JessEmailBackend(BaseEmailBackend):
    """Write each message to the outbox and dispatch it. Never sends inline."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The rows this connection wrote, in order. Lets a caller that wants to link to
        # the outbox find its own message deterministically:
        #
        #     conn = get_connection()      # a JessEmailBackend
        #     email.connection = conn
        #     email.send()
        #     conn.recorded_ids            # -> [pk]
        #
        # Without this, a caller has to guess by querying "the newest row that looks
        # like mine", which two concurrent sends get wrong.
        self.recorded_ids: list[int] = []

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        accepted = 0
        for message in email_messages:
            try:
                row = self._record(message)
            except Exception:
                # Writing the row is the one thing that must not take the caller down:
                # a password-reset POST cannot 500 because the outbox insert failed.
                logger.exception("Jess could not record an outgoing message")
                if not self.fail_silently:
                    raise
                continue
            self.recorded_ids.append(row.pk)
            self._dispatch(row)
            accepted += 1
        return accepted

    # -- internals ---------------------------------------------------------------

    def _record(self, message):
        """One MailMessage row from one EmailMessage.

        One row per MESSAGE, not per recipient: a message to three people is one thing
        that either went out or did not, and splitting it would send three copies.
        """
        from .models import EmailProvider, MailMessage

        html_body = ""
        for content, mimetype in getattr(message, "alternatives", None) or []:
            if mimetype == "text/html":
                html_body = content
                break

        headers = dict(getattr(message, "extra_headers", None) or {})
        purpose = headers.pop(PURPOSE_HEADER, "") or MailMessage.PURPOSE_OTHER
        valid = {choice for choice, _label in MailMessage.PURPOSE_CHOICES}
        if purpose not in valid:
            purpose = MailMessage.PURPOSE_OTHER

        # Attachments are refused rather than stored: keeping them would mean either
        # bloating this row or reaching into the vault, and nothing in the tree sends
        # one. Recorded on the row so it is visible rather than silently dropped.
        note = ""
        if getattr(message, "attachments", None):
            note = (
                f"{len(message.attachments)} attachment(s) were dropped — Jess does not "
                "carry attachments."
            )

        reply_to = ""
        if getattr(message, "reply_to", None):
            reply_to = message.reply_to[0]

        provider = EmailProvider.active_provider()

        return MailMessage.objects.create(
            to=list(message.to or []),
            cc=list(getattr(message, "cc", None) or []),
            bcc=list(getattr(message, "bcc", None) or []),
            subject=message.subject or "",
            body=message.body or "",
            html_body=html_body,
            # Left blank when the caller passed nothing, so the provider's own
            # from_address gets its turn at send time (delivery.send_now).
            from_address=(message.from_email or ""),
            reply_to=reply_to,
            headers=headers,
            provider=provider,
            provider_label=(provider.label if provider else ""),
            purpose=purpose,
            status=MailMessage.QUEUED,
            error=note,
        )

    def _dispatch(self, row):
        """Hand the row to Celery, recording a broker failure instead of raising.

        ``CELERY_BROKER_CONNECTION_TIMEOUT = 3`` bounds this, but a broker that is simply
        down still raises — and a password-reset POST must not 500 because redis blinked.
        So the row is marked failed with the reason and the send still counts as
        accepted: the row is the truth, and the outbox page shows it.
        """
        from django.utils import timezone

        from .models import MailMessage
        from .tasks import send_mail_message

        try:
            send_mail_message.delay(row.pk)
        except Exception as exc:
            logger.warning("Jess could not queue message %s: %s", row.pk, exc)
            MailMessage.objects.filter(pk=row.pk).update(
                status=MailMessage.FAILED,
                error=(
                    f"Could not queue for delivery: {exc}. The message was recorded but "
                    "no worker was reachable — retry it once Celery is up."
                ),
                finished_at=timezone.now(),
            )
