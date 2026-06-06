import base64
import os

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.text import slugify

from toto.core.domain import DomainEntity
from toto.gervazy.crypto import aes_gcm_decrypt, aes_gcm_encrypt

_LOCK_MEMORY_COST = 65536
_LOCK_ITERATIONS = 3
_LOCK_LANES = 4


def _derive_lock_key(password: str, salt: bytes) -> bytes:
    kdf = Argon2id(
        salt=salt,
        length=32,
        iterations=_LOCK_ITERATIONS,
        lanes=_LOCK_LANES,
        memory_cost=_LOCK_MEMORY_COST,
    )
    return kdf.derive(password.encode("utf-8"))


class Category(DomainEntity):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class IdeaBox(DomainEntity):
    """A graph node: a free-text ``label`` plus a ``properties`` bag.

    Everything that used to be a dedicated column (body, source_title,
    source_url, source_type, quote, ...) now lives inside ``properties``.
    ``category`` stays a real relation and the lock state columns stay
    first-class because they are operational, not content.
    """

    label = models.CharField(max_length=160, blank=True)

    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        related_name="idea_boxes",
        null=True,
        blank=True,
    )

    properties = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "All node data: body, source_title, source_url, source_type, "
            "quote, plus any custom keys."
        ),
    )

    is_locked = models.BooleanField(default=False)
    lock_salt = models.BinaryField(null=True, blank=True)
    encrypted_body = models.BinaryField(null=True, blank=True)
    lock_nonce = models.BinaryField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.label or self.body[:80] or "Untitled box"

    # ── Well-known property accessors ───────────────────────────────
    # Convenience read-only views over keys inside ``properties`` so views
    # and templates can keep saying ``box.body`` / ``box.source_title``.
    @property
    def body(self):
        return self.properties.get("body", "")

    @property
    def source_title(self):
        return self.properties.get("source_title", "")

    @property
    def source_url(self):
        return self.properties.get("source_url", "")

    @property
    def source_type(self):
        return self.properties.get("source_type", "")

    @property
    def quote(self):
        return self.properties.get("quote", "")

    def get_property(self, key, default=None):
        return self.properties.get(key, default)

    def set_property(self, key, value):
        self.properties[key] = value
        self.save(update_fields=["properties", "updated_at"])

    def lock(self, password: str) -> None:
        if self.is_locked:
            raise ValueError("Box is already locked.")
        if not password:
            raise ValueError("Password is required.")
        salt = os.urandom(16)
        key = _derive_lock_key(password, salt)
        ciphertext, nonce = aes_gcm_encrypt(key, self.body.encode("utf-8"))
        self.lock_salt = salt
        self.encrypted_body = ciphertext
        self.lock_nonce = nonce
        self.properties["body"] = ""
        self.is_locked = True
        self.save(update_fields=["properties", "is_locked", "lock_salt", "encrypted_body", "lock_nonce", "updated_at"])

    def unlock(self, password: str) -> str:
        if not self.is_locked:
            raise ValueError("Box is not locked.")
        if not password:
            raise ValueError("Password is required.")
        salt = bytes(self.lock_salt)
        key = _derive_lock_key(password, salt)
        plaintext = aes_gcm_decrypt(key, bytes(self.encrypted_body), bytes(self.lock_nonce))
        body = plaintext.decode("utf-8")
        self.properties["body"] = body
        self.is_locked = False
        self.lock_salt = None
        self.encrypted_body = None
        self.lock_nonce = None
        self.save(update_fields=["properties", "is_locked", "lock_salt", "encrypted_body", "lock_nonce", "updated_at"])
        return body


class IdeaLink(DomainEntity):
    from_box = models.ForeignKey(
        IdeaBox,
        on_delete=models.CASCADE,
        related_name="outgoing_links",
    )

    to_box = models.ForeignKey(
        IdeaBox,
        on_delete=models.CASCADE,
        related_name="incoming_links",
    )

    label = models.CharField(
        max_length=80,
        blank=True,
        help_text="Free-text relationship label, e.g. about, supports, contradicts, expands.",
    )

    properties = models.JSONField(
        default=dict,
        blank=True,
        help_text="Flexible metadata for the link.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("from_box", "to_box", "label")]
        ordering = ["-created_at"]

    def __str__(self):
        label = self.label or "related to"
        return f"{self.from_box} → {label} → {self.to_box}"


# External targets a SubjectReference may point at: (app_label, model, human label).
# `model` is the ContentType model name (the lowercased class name).
SUBJECT_REFERENCE_TARGETS = [
    ("kanban", "mission", "Kanban mission"),
    ("kanban", "campaign", "Kanban campaign"),
    ("kanban", "project", "Kanban project"),
    ("core", "federation", "Federation"),
    ("events", "scheduledevent", "Event"),
    ("events", "eventcategory", "Event category"),
    ("locations", "address", "Address"),
    ("locations", "territory", "Territory"),
    ("locations", "route", "Route"),
    ("locations", "zone", "Zone"),
    ("socialhub", "community", "Community"),
    ("people", "person", "Person"),
]


def subject_reference_content_type_limit():
    """`limit_choices_to` for the ContentType FK — restricts it to the allowed targets."""
    q = models.Q()
    for app_label, model_name, _label in SUBJECT_REFERENCE_TARGETS:
        q |= models.Q(app_label=app_label, model=model_name)
    return q


class SubjectReference(DomainEntity):
    """An edge from a bento node out to an external object in another toto app.

    Unlike ``IdeaLink`` (node → node, inside bento), a ``SubjectReference`` reaches
    OUT to one of a fixed set of external models via the contenttypes framework.
    """

    box = models.ForeignKey(
        IdeaBox,
        on_delete=models.CASCADE,
        related_name="references",
    )

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        limit_choices_to=subject_reference_content_type_limit,
        help_text="Which external model the reference points at.",
    )
    # CharField rather than the usual PositiveIntegerField: the targets have mixed
    # PK types — most are bigint, but events.ScheduledEvent has a UUID primary key.
    object_id = models.CharField(max_length=255)
    subject = GenericForeignKey("content_type", "object_id")

    label = models.CharField(
        max_length=80,
        blank=True,
        help_text="Optional relationship label, e.g. references, about, located at.",
    )
    properties = models.JSONField(
        default=dict,
        blank=True,
        help_text="Flexible metadata for the reference.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]
        constraints = [
            models.UniqueConstraint(
                fields=["box", "content_type", "object_id", "label"],
                name="unique_subject_reference",
            )
        ]

    def __str__(self):
        return f"{self.box} → {self.label or 'references'} → {self.subject_label}"

    @property
    def subject_label(self):
        """Human label for the external target, resilient to dangling references."""
        try:
            subject = self.subject
        except Exception:
            subject = None
        if subject is not None:
            return str(subject)
        return f"{self.content_type} #{self.object_id}"

    @property
    def target_model_label(self):
        return self.content_type.name if self.content_type_id else ""
