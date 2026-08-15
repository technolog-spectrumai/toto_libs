"""Mailboxes: real accounts on real mail servers, held two different ways.

This app is **not** a simulation. Every message goes over the account's own
IMAP/SMTP; the platform stores a connection and a credential, nothing else.

**Two custody regimes, and the difference is the whole design.**

A *personal* mailbox is sealed under its owner's own strongbox passphrase
(``gervazy.UserStrongbox``). The platform physically cannot read that mail
without the owner present and typing it — no scheduled job, no operator, no
database dump. The cost is stated rather than hidden: mail syncs only while
they are here.

A *system* mailbox must speak when nobody is logged in — a password reset
fires for somebody who by definition cannot sign in. So its credential lives
in the PLATFORM strongbox (jess's, the same Argon2id envelope), which means
the server can send as it unattended. That is a real reduction in privacy for
exactly one account, it is why designation is superuser-only, and the
designation screen says so in those words.

``keyholder`` records which regime a row is under, so no code has to infer it
from ``owner`` being null.

**Exactly one system mailbox exists**, and the office that governs it is the
Mail Guardian (see :mod:`toto.mail.guardian`). The mailbox belongs to the
platform, not to whoever currently holds the office — access follows the
office, so a handover moves who may read it without touching the row or its
credential.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent

#: The metric every send is recorded against. One code, one price.
METRIC_SEND = "mail.send"


class Keyholder(models.TextChoices):
    """Whose strongbox seals this mailbox's password."""

    PERSON = "person", _("The owner's own strongbox")
    PLATFORM = "platform", _("The platform strongbox")


class MailboxKind(models.TextChoices):
    PERSONAL = "personal", _("Personal")
    SYSTEM = "system", _("System")


class Mailbox(models.Model):
    """One connected mail account.

    The shape of the connection fields deliberately mirrors
    ``jess.EmailProvider`` — same names, same defaults — because operators
    already know that form and because jess's ``delivery``/``receive``
    helpers can then be handed either object.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.CASCADE, related_name="mailboxes",
        help_text=_("Empty for the system mailbox: it belongs to the "
                    "platform, and the Mail Guardian office governs it."))
    kind = models.CharField(max_length=10, choices=MailboxKind.choices,
                            default=MailboxKind.PERSONAL, db_index=True)
    label = models.CharField(max_length=120, help_text=_(
        "What to call this account, e.g. 'Work' or 'Platform mail'."))
    email_address = models.EmailField(help_text=_(
        "The address this account sends as."))

    # -- SMTP (sending) -----------------------------------------------------
    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=587)
    use_tls = models.BooleanField(default=True, help_text=_(
        "STARTTLS, the usual choice on port 587."))
    use_ssl = models.BooleanField(default=False, help_text=_(
        "Implicit TLS, the usual choice on port 465."))
    username = models.CharField(max_length=255, blank=True, help_text=_(
        "The login for both SMTP and IMAP, when they share one."))
    timeout = models.PositiveIntegerField(default=10)

    # -- IMAP (reading) -----------------------------------------------------
    imap_host = models.CharField(max_length=255, blank=True, help_text=_(
        "Leave blank if this account only sends."))
    imap_port = models.PositiveIntegerField(default=993)
    imap_use_ssl = models.BooleanField(default=True)
    folder = models.CharField(max_length=255, default="INBOX", blank=True)

    # -- the credential, which is never a column ----------------------------
    secret = models.ForeignKey(
        "gervazy.EncryptedSecret", null=True, blank=True,
        on_delete=models.PROTECT, related_name="mailboxes")
    keyholder = models.CharField(max_length=10, choices=Keyholder.choices,
                                 default=Keyholder.PERSON)

    is_active = models.BooleanField(default=True)
    #: Stamped by a real connection, never by a page render.
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, help_text=_(
        "What the mail server last said, verbatim."))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "label"]
        constraints = [
            # One account per address per owner. The system row has no owner,
            # so the condition keeps it out of this constraint entirely.
            models.UniqueConstraint(
                fields=["owner", "email_address"],
                condition=models.Q(owner__isnull=False),
                name="mail_one_account_per_address"),
        ]
        verbose_name = _("mailbox")
        verbose_name_plural = _("mailboxes")

    def __str__(self):
        return f"{self.label} <{self.email_address}>"

    def clean(self):
        super().clean()
        if self.use_tls and self.use_ssl:
            raise ValidationError(_(
                "Choose STARTTLS or implicit TLS, not both."))
        if self.host and not self.username:
            raise ValidationError({"username": _(
                "A sending account needs the login its server expects.")})
        if self.kind == MailboxKind.SYSTEM and self.owner_id is not None:
            raise ValidationError(_(
                "The system mailbox belongs to the platform, not to a "
                "person — the Mail Guardian office governs it."))
        if self.kind == MailboxKind.SYSTEM \
                and self.keyholder != Keyholder.PLATFORM:
            raise ValidationError(_(
                "The system mailbox must be sealed by the platform "
                "strongbox: it has to send when nobody is signed in."))
        if self.kind == MailboxKind.PERSONAL and self.owner_id is None:
            raise ValidationError(_("A personal mailbox needs an owner."))

    def save(self, *args, **kwargs):
        # Exactly one system mailbox, enforced here rather than by a partial
        # unique index — the jess one-active-row idiom (jess/models.py:146).
        # A constraint would make replacing the system mailbox a two-step
        # dance that can fail halfway.
        if self.kind == MailboxKind.SYSTEM:
            with transaction.atomic():
                (Mailbox.objects.filter(kind=MailboxKind.SYSTEM)
                 .exclude(pk=self.pk)
                 .update(kind=MailboxKind.PERSONAL, is_active=False))
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    # -- capability questions, asked by name --------------------------------
    @property
    def can_send(self) -> bool:
        return bool(self.host and self.secret_id and self.is_active)

    @property
    def can_receive(self) -> bool:
        return bool(self.imap_host and self.secret_id and self.is_active)

    @property
    def is_system(self) -> bool:
        return self.kind == MailboxKind.SYSTEM


class MailAttachment(models.Model):
    """A file that travelled with a message. The bytes live in the Vault.

    Storing attachments as ``VaultFile`` rows rather than a FileField of our
    own is what buys antivirus screening, the storage levy, the size cap and
    the 404-not-403 download door — none of which this app then has to
    implement. jess refuses attachments outright today
    (``jess/backend.py:104-113``); this is where they land instead.
    """

    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE,
                                related_name="attachments")
    #: The wire identity of the message this belonged to, so an attachment
    #: can be found again without a local copy of the message body.
    message_id = models.CharField(max_length=998, db_index=True)
    vault_file = models.ForeignKey("vault.VaultFile", on_delete=models.CASCADE,
                                   related_name="mail_attachments")
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    is_inbound = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["filename"]
        indexes = [models.Index(fields=["mailbox", "message_id"])]
        verbose_name = _("mail attachment")
        verbose_name_plural = _("mail attachments")

    def __str__(self):
        return self.filename


class SendStatus(models.TextChoices):
    QUEUED = "queued", _("Queued")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Failed")


class SentRecord(models.Model):
    """One row per RECIPIENT of one send — the campaign runway.

    A message to forty people is forty rows here and one message on the
    wire's terms. That shape is deliberate and is the whole preparation for
    campaigns: a campaign is later a UI over these rows plus a way to build
    the recipient list, not a new table and not a second sending path. It
    also makes an ordinary send honest — "sent to three of four, and here is
    which one failed and why" is a sentence this can say today.
    """

    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE,
                                related_name="sent_records")
    message_id = models.CharField(max_length=998, db_index=True)
    recipient = models.EmailField()
    subject = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=SendStatus.choices,
                              default=SendStatus.QUEUED, db_index=True)
    error = models.TextField(blank=True)
    #: The transport row jess wrote for this send, when there is one — so the
    #: operator console and this app never disagree about what happened.
    jess_message_id = models.PositiveIntegerField(null=True, blank=True)
    #: Null today. A campaign later groups its rows by this, with no
    #: migration and no second table.
    campaign_ref = models.CharField(max_length=64, blank=True, db_index=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                blank=True, on_delete=models.SET_NULL,
                                related_name="mail_sent_records")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["mailbox", "-created_at"]),
            models.Index(fields=["status"]),
        ]
        verbose_name = _("sent record")
        verbose_name_plural = _("sent records")

    def __str__(self):
        return f"{self.recipient}: {self.status}"


class MailUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = _("Mail usage event")
        verbose_name_plural = _("Mail usage events")


class MailQuotaPolicy(AbstractQuotaPolicy):
    events = MailUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = _("Mail quota policy")
        verbose_name_plural = _("Mail quota policies")
