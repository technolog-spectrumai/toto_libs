"""Bucket types: one adapter per kind of storage a bucket can live on.

Storage → Management creates, describes, tests and deletes buckets through
this registry and never through an ``if provider == ...``: a view asks the
adapter. The drivers in ``storage_backends`` stay what moves bytes; an adapter
is what an operator sees and fills in, and what a new bucket of its kind needs
to exist (a sealed credential, a pairing, a probe that passed).

The registry is its own (``StorageAdapter.registry``), filled by
``autodiscover_plugins("plugins.storage_adapters")`` in ``VaultConfig.ready``;
the vault's own adapters are in ``plugins/storage_adapters.py``:

| key | backend | what it is |
|---|---|---|
| ``local`` | local | this server's own storage |
| ``aws_s3`` | s3 | an Amazon S3 bucket (preset ``aws``) |
| ``ovh_s3`` | s3 | an OVH Object Storage bucket (preset ``ovh``; the region picks the endpoint) |
| ``zenobia_remote`` | remote_toto | a bucket shared by another Zenobia, connected with its pairing code |
| ``s3`` | s3 | any other S3 bucket made before Management (describe/test/delete only, never offered by Create) |

The interface, in the order a Create modal uses it:

- ``fields()`` — the inputs, as plain dicts (``name``, ``label``, ``kind``,
  ``required``, ``help``, ``choices``, ``secret``). A ``secret`` field is never
  put back in a form, a draft, a log, JSON or an audit record.
- ``validate(data) -> (config, secret)`` — ``config`` is non-secret and may be
  shown and kept in a draft; ``secret`` never. Raises ``ValidationError``
  keyed by field name, with sentences.
- ``probe_candidate(config, secret) -> (ok, message)`` — the connection test
  BEFORE anything is saved (S3 must pass it; ``create`` runs it again).
- ``create(name, owner, actor, config, secret, ...) -> Bucket`` — one
  transaction: the bucket (``created_by`` = the actor), its sealed secret or
  pairing, the audit record.
- ``probe(bucket) -> (ok, message)`` — the Test button, bounded, stamped.
- ``describe(bucket)`` — ``target``, ``status``, ``health`` (and their labels)
  from stamped columns only, never a secret and never the network.
- ``destroy_plan(bucket)`` — what Delete removes and what it never touches.

Deleting is the lifecycle module's (``bucket_lifecycle.request_deletion``):
it is the same for every kind — every file through ``purge.purge_file`` (a
mount's stubs as rows only), then the bucket.
"""

from __future__ import annotations

import re
from typing import ClassVar

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.utils.translation import pgettext

from toto.core.plugin import BasePlugin

#: What ``describe()["status"]`` can be.
STATUS_ACTIVE = "active"
STATUS_DELETING = "deleting"
STATUS_DELETE_FAILED = "delete_failed"
#: Still being deleted, with no reason stamped, long after Delete was last
#: confirmed: the job may have died where no code could say so (a worker
#: killed, a restart mid-purge, a lost queue message). Offers Delete again,
#: which resumes; a job still running is not harmed by a second one (the
#: purge is idempotent).
STATUS_DELETE_STALLED = "delete_stalled"
#: Minutes after the last confirmation at which a purge with no stamped
#: reason is presumed stalled (``VAULT_PURGE_STALL_MINUTES``).
PURGE_STALL_MINUTES = 30

#: What ``describe()["health"]`` can be — tri-state on purpose: "never
#: tested" is a true answer, and a green badge nobody earned would be a lie.
HEALTH_OK = "ok"
HEALTH_ERROR = "error"
HEALTH_UNKNOWN = "unknown"

NAME_MAX = 100


def purge_stalled(bucket, now=None) -> bool:
    """Being deleted, no reason stamped, and Delete last confirmed more than
    ``VAULT_PURGE_STALL_MINUTES`` ago."""
    import datetime

    from django.conf import settings
    from django.utils import timezone

    requested = bucket.deletion_requested_at
    if requested is None or bucket.deletion_error:
        return False
    try:
        minutes = float(getattr(settings, "VAULT_PURGE_STALL_MINUTES", PURGE_STALL_MINUTES))
    except (TypeError, ValueError):
        minutes = PURGE_STALL_MINUTES
    return (now or timezone.now()) - requested >= datetime.timedelta(minutes=minutes)


def status_of(bucket) -> str:
    if bucket.deletion_requested_at is None:
        return STATUS_ACTIVE
    if bucket.deletion_error:
        return STATUS_DELETE_FAILED
    return STATUS_DELETE_STALLED if purge_stalled(bucket) else STATUS_DELETING


def status_label(status: str) -> str:
    return {
        STATUS_ACTIVE: _("Active"),
        STATUS_DELETING: _("Being deleted"),
        STATUS_DELETE_FAILED: _("Deletion stopped"),
        STATUS_DELETE_STALLED: _("Deletion may have stopped"),
    }.get(status, status)


def health_label(health: str) -> str:
    return {
        # "Answered" of a storage that answered its test, with a context of its
        # own: the bare word is the support bot's answer rate in Polish
        # (2026-10-01, 37c.11).
        HEALTH_OK: pgettext("storage health", "Answered"),
        HEALTH_ERROR: _("Failed"),
        HEALTH_UNKNOWN: _("Never tested"),
    }.get(health, health)


def field(name, label, *, kind="text", required=True, help="", choices=None,
          secret=False, placeholder="", max_length=None) -> dict:
    """One input of a Create modal, as the template reads it."""
    return {
        "name": name, "label": label, "kind": kind, "required": required,
        "help": help, "choices": list(choices or []), "secret": secret,
        "placeholder": placeholder, "max_length": max_length,
    }


def _text(data, name) -> str:
    value = data.get(name, "") if hasattr(data, "get") else ""
    return str(value or "").strip()


# ---------------------------------------------------------------------------
# What every kind shares: the name, the owner
# ---------------------------------------------------------------------------

def clean_name(name, *, exclude_pk=None) -> str:
    """A bucket name: present, at most 100 characters, not taken."""
    from .models import Bucket

    name = str(name or "").strip()
    if not name:
        raise ValidationError({"name": _("Give the bucket a name.")})
    if len(name) > NAME_MAX:
        raise ValidationError({"name": _("A bucket name has at most 100 characters.")})
    taken = Bucket.objects.filter(name__iexact=name)
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise ValidationError({"name": _("A bucket with that name already exists.")})
    return name


def clean_owner(owner):
    """The owner: an active account. A bucket is never CREATED ownerless."""
    if owner is None or not getattr(owner, "pk", None):
        raise ValidationError({"owner": _("Choose who owns the bucket.")})
    if not getattr(owner, "is_active", False):
        raise ValidationError({"owner": _("That account is not active.")})
    return owner


def clean_quota(value):
    """``storage_quota_mb``: blank for unlimited, else a positive whole number."""
    if value in (None, ""):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError({"storage_quota_mb": _("The quota is a whole number of MB, or blank.")})
    if number < 1 or number > 2**31 - 1:
        raise ValidationError({"storage_quota_mb": _("The quota is a whole number of MB, or blank.")})
    return number


def unique_slug(name: str) -> str:
    """A free slug for a new bucket, from its name (``bucket`` when the name
    has no letters slugify keeps)."""
    from .models import Bucket

    base = (slugify(name) or "bucket")[:110]
    slug, n = base, 1
    while Bucket.objects.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


def require_field_key():
    """Refuse, with a sentence, to seal anything on a host whose
    FIELD_ENCRYPTION_KEY dies with the process (``models.field_key_configured``)."""
    from .models import field_key_configured

    if not field_key_configured():
        raise ValidationError(_(
            "This server has no permanent FIELD_ENCRYPTION_KEY, so a secret "
            "sealed now could not be opened after the next restart. Set "
            "FIELD_ENCRYPTION_KEY in the deploy configuration first."))


def outbound_url(raw: str, *, field_name: str, label: str) -> str:
    """An address this server may call (``outbound.assert_outbound_allowed``),
    canonical — or a ValidationError on ``field_name`` saying why not."""
    from .outbound import OutboundRefused, assert_outbound_allowed

    try:
        return assert_outbound_allowed(raw, label=label)
    except OutboundRefused as exc:
        raise ValidationError({field_name: str(exc)})


# ---------------------------------------------------------------------------
# The base class
# ---------------------------------------------------------------------------

class StorageAdapter(BasePlugin):
    """One kind of bucket. Subclass, set the class attributes, register with
    ``@StorageAdapter.plugin(key=..., title=..., order=...)`` in an app's
    ``plugins/storage_adapters.py``."""

    registry: ClassVar[dict[str, "StorageAdapter"]] = {}

    #: ``Bucket.storage_backend`` of the buckets this adapter makes.
    backend: ClassVar[str] = "local"
    #: Font Awesome classes for the type picker.
    icon: ClassVar[str] = "fa-solid fa-hard-drive"
    #: The bytes live somewhere else — the list shows the cloud badge
    #: (``vault/partials/_bucket_badge.html``) and the host must allow
    #: external buckets (``VAULT_EXTERNAL_BUCKETS``).
    is_remote: ClassVar[bool] = False
    #: Offered by Create. False for an adapter that only describes rows made
    #: some other way.
    creatable: ClassVar[bool] = True
    #: Create seals something under FIELD_ENCRYPTION_KEY (and refuses without
    #: a permanent one).
    seals_secret: ClassVar[bool] = False
    #: One sentence under the title in the type picker.
    summary: ClassVar[str] = ""

    # -- the registry -------------------------------------------------------

    @classmethod
    def creatable_adapters(cls) -> list["StorageAdapter"]:
        """What Create offers on this host, in order."""
        return [a for a in cls.all() if a.creatable and a.is_available()]

    @classmethod
    def for_key(cls, key: str) -> "StorageAdapter | None":
        adapter = cls.registry.get(str(key or ""))
        if adapter is None or not adapter.creatable or not adapter.is_available():
            return None
        return adapter

    @classmethod
    def for_bucket(cls, bucket) -> "StorageAdapter | None":
        """The adapter that describes an existing bucket (first match in
        order; the generic S3 adapter comes last)."""
        for adapter in cls.all():
            if adapter.matches(bucket):
                return adapter
        return None

    # -- per adapter ----------------------------------------------------------

    def is_available(self) -> bool:
        """Remote kinds exist only where the host allows external buckets."""
        if not self.is_remote:
            return True
        from .models import external_buckets_allowed

        return external_buckets_allowed()

    def matches(self, bucket) -> bool:
        return (bucket.storage_backend or "local") == self.backend

    def fields(self) -> list[dict]:
        """The kind's own inputs (name, owner and quota — and the AI shield
        where the assistant is installed — are common to every kind and
        belong to the view)."""
        return []

    def secret_field_names(self) -> set[str]:
        """Inputs a draft, a log or a page must never carry back."""
        return {f["name"] for f in self.fields() if f.get("secret")}

    def validate(self, data) -> tuple[dict, dict]:
        """``(config, secret)`` from the modal's data, or ValidationError."""
        return {}, {}

    def probe_candidate(self, config: dict, secret: dict) -> tuple[bool, str]:
        """The connection test before anything is saved."""
        return True, _("Nothing to test: the files stay on this server.")

    def probe(self, bucket) -> tuple[bool, str]:
        """The Test button: one bounded check, stamped on the bucket."""
        return self._stamp_probe(bucket, True, _("Stored on this server."))

    def describe(self, bucket) -> dict:
        status = status_of(bucket)
        health = self.health(bucket)
        return {
            "target": self.target(bucket),
            "status": status, "status_label": status_label(status),
            "health": health, "health_label": health_label(health),
            "detail": self.health_detail(bucket),
            "credential": self.credential_label(bucket),
        }

    def target(self, bucket) -> str:
        return _("This server")

    def health(self, bucket) -> str:
        if bucket.last_probe_error:
            return HEALTH_ERROR
        if bucket.last_probe_at:
            return HEALTH_OK
        return HEALTH_UNKNOWN

    def health_detail(self, bucket) -> str:
        return bucket.last_probe_error or ""

    def credential_label(self, bucket) -> str:
        return ""

    def destroy_plan(self, bucket) -> dict:
        """What Delete takes, what it leaves — the Delete modal's text."""
        files, size = file_totals(bucket)
        return {
            "files": files, "bytes": size,
            "removes": [
                _("Every file in the bucket (%(count)s), and its bytes on this server.")
                % {"count": files},
                _("Its folders, upload gateways, clearance keeping and shares with other Zenobias."),
            ],
            "keeps": [],
        }

    # -- create ---------------------------------------------------------------

    def create(self, name, owner, actor, config, secret, *, storage_quota_mb=None,
               ai_protected=False):
        """Make the bucket in one transaction and return it."""
        from . import bucket_lifecycle
        from .models import Bucket

        if not self.is_available():
            raise ValidationError(_("External buckets are disabled on this server."))
        name = clean_name(name)
        owner = clean_owner(owner)
        quota = clean_quota(storage_quota_mb)
        config = dict(config or {})
        secret = dict(secret or {})
        if self.seals_secret:
            require_field_key()
        self.before_create(config, secret)
        with transaction.atomic():
            bucket = Bucket(
                name=name, slug=unique_slug(name), owner=owner, created_by=actor,
                storage_quota_mb=quota,
                # The assistant's field: its default on a host without one.
                ai_protected=bool(ai_protected) and bucket_lifecycle.shield_offered(),
                storage_backend=self.backend,
            )
            self.build(bucket, config, secret)
            bucket.full_clean()
            bucket.save()
            self.store_secret(bucket, config, secret)
            bucket_lifecycle.record_created(bucket, actor, adapter=self)
        self.after_create(bucket, config, secret)
        return bucket

    def before_create(self, config: dict, secret: dict) -> None:
        """Checks that need the network (a probe) — outside the transaction."""

    def build(self, bucket, config: dict, secret: dict) -> None:
        """Fill the unsaved bucket from the validated config."""

    def store_secret(self, bucket, config: dict, secret: dict) -> None:
        """Seal what must be sealed, inside the create transaction."""

    def after_create(self, bucket, config: dict, secret: dict) -> None:
        """After the commit (a mount's first probe)."""

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _stamp_probe(bucket, ok: bool, message: str) -> tuple[bool, str]:
        from django.utils import timezone

        from .models import Bucket

        now = timezone.now()
        error = "" if ok else (message or _("The test failed."))[:2000]
        if bucket.pk:
            Bucket.objects.filter(pk=bucket.pk).update(last_probe_at=now, last_probe_error=error)
        bucket.last_probe_at, bucket.last_probe_error = now, error
        return ok, message


def file_totals(bucket) -> tuple[int, int]:
    from django.db.models import Count, Sum

    from .models import VaultFile

    # Trashed files too (2026-10-01): their bytes are still held.
    row = VaultFile.all_objects.filter(bucket=bucket).aggregate(n=Count("pk"), size=Sum("file_size_bytes"))
    return row["n"] or 0, row["size"] or 0


# ---------------------------------------------------------------------------
# S3 validation, shared by the S3 adapters
# ---------------------------------------------------------------------------

#: S3's own bucket-naming rule: 3–63 characters, lower-case letters, digits,
#: dots and hyphens, starting and ending with a letter or digit.
_S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_S3_PREFIX = re.compile(r"^[A-Za-z0-9._\-/]*$")
_ACCESS_KEY_ID = re.compile(r"^[A-Za-z0-9]{8,128}$")


def clean_s3_bucket_name(raw) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValidationError({"bucket_name": _("Enter the bucket's name at the provider.")})
    if not _S3_BUCKET.fullmatch(value) or ".." in value:
        raise ValidationError({"bucket_name": _(
            "An S3 bucket name has 3 to 63 characters: lower-case letters, "
            "digits, dots and hyphens, starting and ending with a letter or digit.")})
    return value


def clean_s3_prefix(raw) -> str:
    """``vault/`` when blank; otherwise the path with one trailing slash."""
    value = str(raw or "").strip().strip("/")
    if not value:
        return "vault/"
    if len(value) > 200 or not _S3_PREFIX.fullmatch(value) or ".." in value.split("/") \
            or "//" in value:
        raise ValidationError({"prefix": _(
            "A prefix is a path such as vault/ or team/files/: letters, digits, "
            "dots, hyphens, underscores and slashes.")})
    return value + "/"


def clean_s3_keys(data) -> dict:
    key_id = _text(data, "access_key_id")
    key_secret = str(data.get("secret_access_key", "") or "").strip() if hasattr(data, "get") else ""
    errors = {}
    if not key_id:
        errors["access_key_id"] = _("Enter the access key id.")
    elif not _ACCESS_KEY_ID.fullmatch(key_id):
        errors["access_key_id"] = _("An access key id is 8 to 128 letters and digits.")
    if not key_secret:
        errors["secret_access_key"] = _("Enter the secret key.")
    elif len(key_secret) > 256 or any(ch.isspace() for ch in key_secret):
        errors["secret_access_key"] = _("The secret key has no spaces and at most 256 characters.")
    if errors:
        raise ValidationError(errors)
    return {"aws_access_key_id": key_id, "aws_secret_access_key": key_secret}
