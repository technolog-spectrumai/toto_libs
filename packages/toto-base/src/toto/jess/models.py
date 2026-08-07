"""Jess's two tables: how mail leaves, and what left.

``EmailProvider`` is the transport. ``MailMessage`` is the outbox — one row per message,
written by the backend before anything is dispatched, so a message that never goes out is
visible rather than lost.

The outbox is deliberately the foundation of a managed auto-email service rather than a
debug log, which is why ``purpose`` exists from day one: it is what lets a later feature
report and rate-limit per stream without a migration. Templates, schedules, recipient
lists and bounce handling are NOT modelled — they arrive when something needs them.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent

# --- how a provider actually sends -----------------------------------------------
# The dotted paths are Django's own backends. Jess's own backend is deliberately NOT
# in this map: a provider claiming it would make delivery.py re-enter the queue and
# loop forever. delivery.build_connection() asserts that too, belt and braces.
BACKEND_SMTP = "smtp"
BACKEND_CONSOLE = "console"
BACKEND_DUMMY = "dummy"
BACKEND_LOCMEM = "locmem"
BACKEND_FILEBASED = "filebased"

BACKEND_CHOICES = [
    (BACKEND_SMTP, "SMTP"),
    (BACKEND_CONSOLE, "Console (printed, not sent)"),
    (BACKEND_DUMMY, "Dummy (discarded)"),
    (BACKEND_LOCMEM, "In-memory (tests)"),
    (BACKEND_FILEBASED, "Files on disk"),
]

BACKEND_PATHS = {
    BACKEND_SMTP: "django.core.mail.backends.smtp.EmailBackend",
    BACKEND_CONSOLE: "django.core.mail.backends.console.EmailBackend",
    BACKEND_DUMMY: "django.core.mail.backends.dummy.EmailBackend",
    BACKEND_LOCMEM: "django.core.mail.backends.locmem.EmailBackend",
    BACKEND_FILEBASED: "django.core.mail.backends.filebased.EmailBackend",
}

# Which of them can put a message in front of a human. The same distinction
# toto.core.email_config draws (its _NON_DELIVERING_EMAIL_BACKENDS), and the reason
# locmem counts as delivering there too: tests assert against mail.outbox.
DELIVERING_BACKENDS = {BACKEND_SMTP, BACKEND_LOCMEM, BACKEND_FILEBASED}


class EmailProvider(models.Model):
    """How mail leaves this platform. Several may exist; exactly one is ``active``.

    Mirrors ``sso_client.OIDCProviderConfig``: switching provider is flipping a flag,
    and the row you switched away from is still there to switch back to. A console row
    can sit beside a real SMTP row so a developer does not have to destroy the working
    configuration to test something.

    The password is a ``gervazy.EncryptedSecret``, not a column. Read
    ``jess/README.md`` for why the environment is the wrong place for it — the short
    version is that ``deploy.py`` copies config env into ``.env`` with no redaction.
    """

    label = models.CharField(
        max_length=120,
        help_text="Display name, e.g. 'Fastmail (production)'.",
    )
    backend = models.CharField(
        max_length=20, choices=BACKEND_CHOICES, default=BACKEND_CONSOLE,
        help_text="How to send. Console and Dummy do not deliver, and Jess reports "
                  "that honestly — the 'Forgot password?' link hides itself.",
    )

    # --- SMTP transport ---
    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=587)
    use_tls = models.BooleanField(default=True, help_text="STARTTLS on port 587.")
    use_ssl = models.BooleanField(default=False, help_text="Implicit TLS on port 465.")
    # Not decorative: an unbounded SMTP connect inside a worker holds it for as long as
    # the far end feels like taking, which is what the platform's timeout ladder exists
    # to prevent. Django's own default here is None, i.e. no limit.
    timeout = models.PositiveIntegerField(
        default=10, help_text="Seconds. Django's default is no limit, which is wrong "
                              "for a worker.",
    )
    username = models.CharField(max_length=255, blank=True)
    secret = models.ForeignKey(
        "gervazy.EncryptedSecret",
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="jess_providers",
        help_text="Gervazy EncryptedSecret holding the SMTP password.",
    )

    # --- identity on the wire ---
    # from_address is what makes DEFAULT_FROM_EMAIL stop mattering. Django's
    # PasswordResetForm.save() passes no from_email, so today every reset email is sent
    # as whatever that setting happens to be.
    from_address = models.EmailField(
        blank=True,
        help_text="The From: address. Falls back to DEFAULT_FROM_EMAIL when empty.",
    )
    reply_to = models.EmailField(blank=True)

    # --- IMAP (inbound): the same account, read as well as written ---
    # Optional. Fill these in and the staff inbox can fetch mail this account has
    # received, on demand. The `username` and the stored `secret` above are reused —
    # one account, both directions — so there is nothing else to configure.
    imap_host = models.CharField(
        max_length=255, blank=True,
        help_text="e.g. imap.fastmail.com. Leave blank if this account is send-only.",
    )
    imap_port = models.PositiveIntegerField(default=993, blank=True)
    imap_use_ssl = models.BooleanField(default=True, help_text="Implicit TLS on port 993.")
    mailbox = models.CharField(max_length=255, default="INBOX", blank=True)

    active = models.BooleanField(
        default=False,
        help_text="Exactly one provider is active; activating this one deactivates "
                  "the others.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-active", "label"]
        verbose_name = "Email provider"
        verbose_name_plural = "Email providers"

    def __str__(self):
        state = " (active)" if self.active else ""
        return f"{self.label} [{self.get_backend_display()}]{state}"

    def clean(self):
        # Both of these are things Django would otherwise raise about at connect time,
        # i.e. inside a worker, minutes later, as a failed send.
        if self.backend == BACKEND_SMTP and not (self.host or "").strip():
            raise ValidationError({"host": "SMTP needs a host."})
        if self.use_tls and self.use_ssl:
            raise ValidationError(
                {"use_ssl": "Pick one of STARTTLS (587) or implicit TLS (465), not both "
                            "— Django refuses the combination when it connects."}
            )

    def save(self, *args, **kwargs):
        """Persist, keeping the one-active-row invariant.

        Done here rather than with a database constraint because a partial unique index
        on ``active`` would make *deactivating in order to activate another* a two-step
        dance that can fail halfway. Same approach as the OIDC bundle import
        (``sso_client/admin.py:63``), which deactivates the rest and then creates.
        """
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.active:
                EmailProvider.objects.filter(active=True).exclude(pk=self.pk).update(
                    active=False
                )

    @property
    def delivers(self) -> bool:
        """Could this provider put a message in front of a human?"""
        return self.backend in DELIVERING_BACKENDS

    @property
    def needs_secret(self) -> bool:
        """Is a password required for this provider to authenticate?"""
        return self.backend == BACKEND_SMTP and bool((self.username or "").strip())

    @property
    def can_receive(self) -> bool:
        """Configured to fetch inbound mail? Needs an IMAP host and a stored password."""
        return bool((self.imap_host or "").strip()) and bool(self.secret_id)

    @classmethod
    def active_provider(cls):
        """The active provider, or None. Read per send — never cached."""
        return cls.objects.filter(active=True).order_by("-updated_at").first()


class MailMessage(models.Model):
    """One outbound message, and what happened to it.

    Written by ``JessEmailBackend`` BEFORE anything is dispatched, so the row is the
    truth even when the broker is down. Statuses and the timestamp/task_id shape are
    copied from ``fileservices.FileServiceRun`` (``fileservices/models.py:5-53``)
    rather than invented, because the polling page is copied from its run page too.
    """

    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    # Recorded but deliberately not dispatched: manual-release custody
    # (JESS_MANUAL_RELEASE) holds every message until an admin types the passphrase.
    # Non-terminal — it is a resting state waiting on a human, not a failure.
    HELD = "held"

    STATUS_CHOICES = [
        (QUEUED, "Queued"),
        (SENDING, "Sending"),
        (SENT, "Sent"),
        (FAILED, "Failed"),
        (HELD, "Held"),
    ]
    # What the poller stops on. HELD is intentionally absent: a held row is not going
    # anywhere until a human releases it, so the detail page shows a release control
    # rather than a live spinner.
    TERMINAL = {SENT, FAILED}

    # Which stream this belongs to. The one piece of future-proofing paid for now: a
    # managed service needs to report and rate-limit per stream, and adding this later
    # would be a migration over a table that by then has history.
    PURPOSE_TEST = "test"
    PURPOSE_MANUAL = "manual"
    PURPOSE_PASSWORD_RESET = "password_reset"
    PURPOSE_SOCIALHUB_ENDORSEMENT = "socialhub_endorsement"
    PURPOSE_OTHER = "other"

    PURPOSE_CHOICES = [
        (PURPOSE_TEST, "Test"),
        (PURPOSE_MANUAL, "Sent by hand"),
        (PURPOSE_PASSWORD_RESET, "Password reset"),
        (PURPOSE_SOCIALHUB_ENDORSEMENT, "Endorsement"),
        (PURPOSE_OTHER, "Other"),
    ]

    # --- the message ---
    # Lists rather than comma-joined strings: an address with a display name can contain
    # a comma, so splitting one back apart is lossy.
    to = models.JSONField(default=list)
    cc = models.JSONField(default=list, blank=True)
    bcc = models.JSONField(default=list, blank=True)
    subject = models.TextField(blank=True)
    body = models.TextField(blank=True)
    html_body = models.TextField(blank=True)
    from_address = models.CharField(max_length=255, blank=True)
    # A list, like to/cc/bcc and like Django's own EmailMessage.reply_to, rather than one
    # address. RFC 5322 allows several, and a single column would mean the backend
    # silently dropped the rest — the same lossiness that made a comma-joined recipient
    # field wrong.
    reply_to = models.JSONField(default=list, blank=True)
    headers = models.JSONField(default=dict, blank=True)

    # --- routing ---
    # SET_NULL, not PROTECT: the row is an audit trail and must outlive the provider it
    # went through. Which provider sent it is still recorded in `error`/history terms by
    # the label captured at send time.
    provider = models.ForeignKey(
        EmailProvider, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="messages",
        help_text="Left empty to use whichever provider is active at send time.",
    )
    provider_label = models.CharField(
        max_length=120, blank=True,
        help_text="The provider's label as it was when this was sent — survives the "
                  "provider being renamed or deleted.",
    )
    purpose = models.CharField(
        max_length=40, choices=PURPOSE_CHOICES, default=PURPOSE_OTHER,
    )

    # --- what happened ---
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=QUEUED, db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(
        blank=True,
        help_text="The provider's own error text, kept verbatim. Diagnosing SMTP is "
                  "exactly when a paraphrase is useless.",
    )
    task_id = models.CharField(max_length=255, blank=True)

    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # SET_NULL and nullable: the password-reset path has no acting user, and the row
    # must survive the account being deleted.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="jess_messages",
    )
    # Who typed the passphrase that released this message, and when. Distinct from
    # created_by: under manual release a password-reset is composed by nobody
    # (created_by is null) and released later by an admin — the audit trail needs both.
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="jess_released",
    )
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-queued_at"]
        indexes = [models.Index(fields=["status", "queued_at"])]
        verbose_name = "Mail message"
        verbose_name_plural = "Mail messages"

    def __str__(self):
        who = ", ".join(self.to or []) or "(no recipient)"
        return f"{self.subject or '(no subject)'} → {who} [{self.status}]"

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL

    @property
    def recipients(self) -> list[str]:
        """Everyone this goes to, for display. Includes bcc — this page is staff-only."""
        return [*(self.to or []), *(self.cc or []), *(self.bcc or [])]

    @property
    def body_is_sensitive(self) -> bool:
        """Is the body a bearer credential that staff must never read?

        A password-reset email contains a one-time reset link: anyone who reads it can
        take over that account. Under manual release an admin *releases* such a message
        (causes it to send) but must never *see* it, so every staff-facing surface hides
        the body and html of these rows. The recipient and status stay visible so the
        release is still auditable.
        """
        return self.purpose == self.PURPOSE_PASSWORD_RESET


class InboundMessage(models.Model):
    """One received message — the inbox, the mirror of ``MailMessage``.

    Fetched on demand from the account's IMAP mailbox (``jess/receive.py``), never by a
    background poller: on a manual-release host there is no server-side passphrase for an
    unattended job to log in with, so a human triggers the fetch and — under manual
    release — types the passphrase, exactly as they do to release outbound mail.

    Deduplicated by ``message_id`` so a re-fetch is idempotent. Threading is by
    ``message_id`` / ``in_reply_to``; a reply is an ordinary outbound ``MailMessage``
    linked back here through ``replied_with``.
    """

    from_address = models.CharField(max_length=255, blank=True)
    to = models.JSONField(default=list, blank=True)
    subject = models.TextField(blank=True)
    body = models.TextField(blank=True)
    html_body = models.TextField(blank=True)
    headers = models.JSONField(default=dict, blank=True)

    # The RFC Message-ID, unique so a re-fetch cannot duplicate a row. A (rare,
    # non-conformant) message with no Message-ID gets a synthesised one at fetch time.
    message_id = models.CharField(max_length=998, unique=True)
    in_reply_to = models.CharField(max_length=998, blank=True)
    references = models.JSONField(default=list, blank=True)

    # The account it was fetched through. SET_NULL like MailMessage.provider — received
    # mail outlives the provider being reconfigured or removed.
    provider = models.ForeignKey(
        EmailProvider, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="inbound",
    )

    received_at = models.DateTimeField(auto_now_add=True)
    # The message's own Date: header — not the same as when we fetched it.
    date_header = models.DateTimeField(null=True, blank=True)

    read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    read_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="jess_read",
    )

    # The outbound reply sent from here, if any — a plain link into the outbox.
    replied_with = models.ForeignKey(
        MailMessage, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reply_to_inbound",
    )

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["read", "received_at"])]
        verbose_name = "Inbound message"
        verbose_name_plural = "Inbound messages"

    def __str__(self):
        return f"{self.subject or '(no subject)'} ← {self.from_address or '(unknown)'}"


# --------------------------------------------------------------------------- #
# Metering                                                                     #
# --------------------------------------------------------------------------- #
# toto.quota owns no tables, so each metered app declares its own concrete pair
# and the rows live in that app's migrations. See toto/quota/models.py.
# Metric: jess.send — and see metrics.py for why the EMAIL_BACKEND path is not
# metered at all.

class JessUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Jess usage event"
        verbose_name_plural = "Jess usage events"


class JessQuotaPolicy(AbstractQuotaPolicy):
    events = JessUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Jess quota policy"
        verbose_name_plural = "Jess quota policies"
