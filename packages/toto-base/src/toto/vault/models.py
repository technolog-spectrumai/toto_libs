import hashlib
import os
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.utils.translation import gettext

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent
from toto.vault.strategy.pdf import PdfStrategy
from toto.vault.strategy.image import ImageStrategy
from toto.vault.strategy.text import TextStrategy
from django.urls import reverse


from .storage import private_storage


class StorageBackend(models.TextChoices):
    LOCAL = "local", "Local"
    S3 = "s3", "S3-compatible"
    REMOTE_TOTO = "remote_toto", "Remote Toto Server"


def external_buckets_allowed() -> bool:
    """Host contract flag (LOCATIONS_GEOCODING / USE_EXTERNAL_FONTS register):
    a host sets ``VAULT_EXTERNAL_BUCKETS = False`` (faros does) to forbid any
    bucket whose storage is not this server's own filesystem — no S3, no
    remote-toto proxying, no public CDN base URLs. Default keeps the full
    backend matrix (zenobia unchanged)."""
    return getattr(settings, "VAULT_EXTERNAL_BUCKETS", True)


def field_key_configured() -> bool:
    """Is ``FIELD_ENCRYPTION_KEY`` a key this host will still have tomorrow?

    Secrets sealed in the database (a bucket's S3 keys, a peer's api key) are
    only as durable as the key that sealed them. Zenobia's settings fall back
    to a RANDOM key per process when the environment names none — fine for a
    peer key re-pairable in a minute, fatal for an S3 secret nobody can type
    again. So a door that seals a secret asks this first and refuses when the
    answer is no.

    Configured means: the setting is non-empty AND it is the value the
    environment carries (the random fallback never is), or the host says so
    explicitly with ``VAULT_FIELD_KEY_PERSISTENT = True`` (a host that sets
    the key from a secrets file rather than the environment).
    """
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
    if not key:
        return False
    if getattr(settings, "VAULT_FIELD_KEY_PERSISTENT", False):
        return True
    if isinstance(key, bytes):
        key = key.decode()
    return os.environ.get("FIELD_ENCRYPTION_KEY", "") == key


def file_edits_allowed() -> bool:
    """Host contract flag: a host sets ``VAULT_FILE_EDITS = False`` (faros
    does) to refuse every server-side rewrite of stored file CONTENT — the
    browser editors, the desktop content API, empty-file creation. Upload,
    download, delete, encrypt/decrypt and zip stay available."""
    return getattr(settings, "VAULT_FILE_EDITS", True)


def storage_only() -> bool:
    """Host contract flag: a host sets ``VAULT_STORAGE_ONLY = True`` (zenobia
    does, since 2026-10-03) to say the vault stores files and shows none.

    Then nothing is playable and nothing is made here: the pages draw no Play,
    no Edit, no image viewer and no "New" (whatever plugin is registered), the
    empty-file doors answer 404, and a file arrives by upload and leaves by
    download. Folders are still made (they are not files). The default, False,
    is every other host's vault as it was."""
    return bool(getattr(settings, "VAULT_STORAGE_ONLY", False))


def refused_file_types() -> frozenset:
    """Host contract flag: ``VAULT_REFUSED_FILE_TYPES = {"latex"}`` names
    vault file types this host refuses at every door that assigns one —
    the three uploads (gateway, API, peer), rename, and empty-file
    creation. Detection stays honest so the refusal can name the type;
    rows that predate the ban keep working. Empty by default."""
    return frozenset(getattr(settings, "VAULT_REFUSED_FILE_TYPES", ()) or ())


# No Microsoft Office file enters the platform (the owner, 2026-09-30: ".docx,
# .xlsx or .pptx ---> REJECT. NO Microsoft here."). Not a host flag: every
# host, every door. OpenDocument (.odt .ods .odp) is not Microsoft and is not
# here. Before this an OOXML upload was refused only by accident — its MIME
# type contains "xml", so detect_type said 'xml' and the XML screener choked
# on the zip — with a sentence that said nothing about why.
OFFICE_EXTENSIONS = frozenset({
    ".docx", ".xlsx", ".pptx",
    ".docm", ".xlsm", ".pptm", ".dotx", ".xltx", ".potx",
    ".doc", ".xls", ".ppt",
})
_OFFICE_MIME_MARKS = ("msword", "officedocument", "ms-excel", "ms-powerpoint",
                      "openxmlformats", "ms-word")
#: The old binary formats are OLE2 compound files.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: An OOXML package is a zip with [Content_Types].xml and one of these parts.
_OOXML_PARTS = ("word/", "xl/", "ppt/")


def office_refusal_sentence() -> str:
    return gettext("Microsoft Office files are not accepted here. Save it as PDF, "
                   "HTML, Markdown or CSV (or OpenDocument) and upload that.")


def _office_content(content) -> bool:
    """Bytes, or a seekable file object — read from its start, whatever
    a caller already read, and left where it was found."""
    import io
    import zipfile

    if content is None:
        return False
    stream = io.BytesIO(content) if isinstance(content, (bytes, bytearray)) else content
    try:
        start = stream.tell()
    except (AttributeError, OSError, ValueError):
        return False
    try:
        stream.seek(0)
        head = stream.read(8)
        if isinstance(head, str):
            return False
        if head.startswith(_OLE2_MAGIC):
            return True
        if not head.startswith(b"PK"):
            return False
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as zf:
                names = zf.namelist()
        except (zipfile.BadZipFile, OSError, ValueError, EOFError):
            return False
        return ("[Content_Types].xml" in names
                and any(n.startswith(_OOXML_PARTS) for n in names))
    finally:
        try:
            stream.seek(start)
        except (OSError, ValueError):
            pass


def is_office_file(filename: str = "", content=None, mime: str = "") -> bool:
    """By name, by declared type, or by what the bytes are — so a .docx
    renamed to .zip is still a .docx, and an .odt (a zip without
    [Content_Types].xml) is not one."""
    if os.path.splitext(filename or "")[1].lower() in OFFICE_EXTENSIONS:
        return True
    if content is not None:
        # The bytes decide when there are bytes. A declared type is the
        # sender's guess, and Windows declares application/vnd.ms-excel for
        # every .csv where Excel is installed: CSV is what the refusal tells
        # people to send instead (2026-10-01).
        return _office_content(content)
    mime = (mime or "").lower()
    return bool(mime) and any(mark in mime for mark in _OFFICE_MIME_MARKS)


def upload_refusal(filename: str = "", *, file_type: str = "", content=None,
                   mime: str = "") -> str:
    """The one rule a door asks before a file enters the vault: '' when it
    may, otherwise the sentence to show. Office files first (never, anywhere),
    then the host's ``VAULT_REFUSED_FILE_TYPES``."""
    if is_office_file(filename, content, mime):
        return office_refusal_sentence()
    if file_type and file_type in refused_file_types():
        return gettext("This host does not accept %(type)s files.") % {"type": file_type}
    return ""


class StorageProvider(models.Model):
    """
    A named S3-compatible provider preset (AWS, OVH, MinIO, …).
    Seeded by ingress_storage_providers; user-extensible via admin.
    """

    name = models.SlugField(max_length=64, unique=True)
    display_name = models.CharField(max_length=128)
    endpoint_url_template = models.CharField(
        max_length=256,
        blank=True,
        help_text=(
            "Endpoint URL, optionally with {region} or {account_id} placeholders. "
            "Leave blank for AWS default routing."
        ),
    )
    default_region = models.CharField(max_length=64, blank=True)
    addressing_style = models.CharField(
        max_length=8,
        choices=[("path", "Path"), ("virtual", "Virtual"), ("auto", "Auto")],
        default="auto",
    )
    use_ssl = models.BooleanField(default=True)
    is_builtin = models.BooleanField(
        default=True,
        help_text="Seeded by ingress — safe to re-run ingress to reset.",
    )

    class Meta:
        verbose_name = "Storage Provider"
        verbose_name_plural = "Storage Providers"
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

    def resolve_endpoint_url(self, region: str = "", account_id: str = "", **kwargs) -> str:
        """Interpolate placeholders in the endpoint template. Returns empty string for AWS."""
        if not self.endpoint_url_template:
            return ""
        try:
            return self.endpoint_url_template.format(
                region=region or self.default_region,
                account_id=account_id,
                **kwargs,
            )
        except KeyError:
            return self.endpoint_url_template


class Bucket(models.Model):
    name = models.CharField(max_length=100, unique=True)
    #: Who the bucket belongs to. SET_NULL since 2026-09-30 (the owner's
    #: decision): deleting an account no longer deletes the buckets it owned —
    #: a bucket holds other people's files, gateways and clearance keeping,
    #: and only a deliberate delete (Storage → Management, a background purge)
    #: may take those. An OWNERLESS bucket grants nothing to anybody through
    #: the "bucket owner" clause (every such check compares against a real
    #: user's pk, which is never None), writes that need an owner refuse with
    #: a sentence, and a superuser on the Superuser plan gives it a new owner.
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    #: Who made it (Management records the actor; older rows and buckets made
    #: by code carry nothing). Immutable: Edit never offers it.
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="buckets_created", editable=False)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    slug = models.SlugField(max_length=120, unique=True)
    storage_quota_mb = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Storage quota per user in MB. Leave blank for unlimited.",
    )
    storage_backend = models.CharField(
        max_length=16,
        choices=StorageBackend.choices,
        default=StorageBackend.LOCAL,
        help_text="Storage backend for files in this bucket.",
    )
    storage_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Non-secret backend config. "
            "S3: bucket_name, region_name, prefix, use_ssl, addressing_style, aws_profile. "
            "remote_toto: server_url, bucket_slug, api_token_env. "
            "Credentials must come from environment variables, not this field."
        ),
    )
    provider = models.ForeignKey(
        StorageProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="buckets",
        help_text="Provider preset used when storage_backend is S3-compatible.",
    )
    peer = models.ForeignKey(
        "vault.BucketPeer",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="buckets",
        help_text=(
            "For remote_toto buckets: the paired host this bucket is mounted "
            "from. PROTECT — a peer with mounted buckets cannot be deleted."
        ),
    )
    public_base_url = models.URLField(
        blank=True,
        default="",
        help_text=(
            "Optional CDN or public base URL (e.g. https://cdn.example.com/vault/). "
            "When set, get_public_file_url() returns a direct link per file."
        ),
    )
    #: How this bucket's storage credentials are obtained.
    #:
    #: ``ambient`` is what every bucket did before sealed credentials existed
    #: and is the default for exactly that reason: boto3's own chain (env vars,
    #: ~/.aws, IAM role) for S3, and the Fernet-sealed BucketPeer.api_key_encrypted
    #: for a mount. Nothing about an existing row changes.
    #:
    #: ``sealed`` means the credential lives in a RemoteCredential, encrypted
    #: under an operator's storage PIN, and every action that needs it asks for
    #: that PIN. Opt in per bucket.
    credential_mode = models.CharField(
        max_length=16,
        choices=(("ambient", "Ambient (environment / legacy)"),
                 ("sealed", "Sealed under a storage PIN")),
        default="ambient",
        help_text="Sealed credentials are opened per action by an operator's "
                  "storage PIN and are never recoverable from a database dump.",
    )
    #: When a remote_toto bucket's mirror last completed. Listings render from
    #: local stub rows, so this stamp is the honest answer to "as of when?" —
    #: a page never probes the peer to find out.
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    #: The AI shield. A protected bucket's files are never offered to the
    #: assistant and never readable by it: the editors drop their AI buttons,
    #: the file wand disappears from the service menu, and the file-ask page
    #: refuses outright. Enforced through toto.core.assistant.allowed_for_file
    #: — one rule, consulted by every door — because a shield with a side
    #: entrance is not a shield.
    ai_protected = models.BooleanField(
        default=False,
        help_text=(
            "The assistant never reads files in this bucket — no AI buttons "
            "in editors, no file wand, no exceptions."
        ),
    )
    #: The last connection test (Management's Test button, and the probe a
    #: new S3 bucket must pass before it is saved). Stamped only by an
    #: operator's click — no page render ever probes. A mount's health lives
    #: on its BucketPeer (last_ok_at / last_error) instead.
    last_probe_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_probe_error = models.TextField(blank=True, default="", editable=False)
    #: Set when a superuser confirmed Delete: the background purge is taking
    #: its files (rows and bytes) and then the bucket. A bucket in this state
    #: is listed as being deleted and refuses Edit.
    deletion_requested_at = models.DateTimeField(null=True, blank=True, editable=False)
    #: Why the purge stopped, when it did (a file another app still pins, a
    #: store that refused). The bucket stays "being deleted" and Delete may be
    #: confirmed again once the cause is fixed.
    deletion_error = models.TextField(blank=True, default="", editable=False)

    class Meta:
        verbose_name = "Bucket"
        verbose_name_plural = "Buckets"
        unique_together = ('name', 'owner')

    def __str__(self):
        return f"Bucket {self.name}"

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Remember what the row said when we read it, so clean() can tell a
        # backend CHANGE from a backend that was always this value.
        instance._loaded_storage_backend = instance.storage_backend
        return instance

    def clean(self):
        super().clean()
        # Model-level belt for the host contract: whatever code path tries to
        # persist a non-local bucket on a local-only host must fail loudly.
        if self.storage_backend != StorageBackend.LOCAL and not external_buckets_allowed():
            raise ValidationError("External buckets are disabled on this host.")
        # A bucket's backend is immutable once it holds files. Flipping it
        # moves money in BOTH directions — StorageLevy._billable excludes only
        # remote_toto and attribution is VaultFile.owner, so local->remote_toto
        # silently stops billing bytes that are still on this disk, and
        # remote_toto->local starts billing owners for stubs whose bytes live
        # on another host. It also changes an access decision (access.py's
        # local_content_q) and points the driver at a store that does not hold
        # the existing objects.
        #
        # This is reached by every ModelForm and by the admin. It is NOT
        # reached by .save() or .update(), deliberately: a data migration must
        # still be able to do it.
        loaded = getattr(self, "_loaded_storage_backend", None)
        if self.pk and loaded is not None and loaded != self.storage_backend:
            held = self.files.count()
            if held:
                raise ValidationError(
                    f"This bucket holds {held} file(s). Changing its backend "
                    f"from '{loaded}' to '{self.storage_backend}' would change "
                    "who pays for those bytes and whether they can be edited. "
                    "Move the files to a new bucket instead.")
        # A mounted bucket without a live pairing is a mount to nowhere; the
        # peer row is the only transport identity there is (no URL, no secret
        # ever lives in storage_config).
        if self.storage_backend == StorageBackend.REMOTE_TOTO:
            if not self.peer_id:
                raise ValidationError(
                    "A remote Toto bucket needs a bucket peer — pair one in "
                    "the admin and select it here.")
            if not self.peer.is_active:
                raise ValidationError(
                    f"Bucket peer '{self.peer.label}' is deactivated.")

    @property
    def is_being_deleted(self) -> bool:
        return self.deletion_requested_at is not None

    @property
    def is_local(self) -> bool:
        """This bucket's bytes are on this host's disk.

        The one question every content-rewrite surface asks — editors, zip,
        encrypt, versions — because those paths open ``VaultFile.file``
        directly and a non-local file there is someone else's bytes behind a
        wire. Blank means local: rows predating the backend field carry "".
        """
        return self.storage_backend in ("", StorageBackend.LOCAL)

    @property
    def is_remote(self) -> bool:
        """The badge question: are this bucket's bytes elsewhere?"""
        return not self.is_local

    @property
    def remote_label(self) -> str:
        """What the badge says after "Remote · ". NEVER a host name or URL —
        bucket badges render in listings any member can see, and where the
        bytes live is operator information (the peer admin has it). Peer
        label / provider display name / generic fallbacks only; templates
        never parse storage_config.
        """
        if not self.is_remote:
            return ""
        if self.storage_backend == StorageBackend.REMOTE_TOTO:
            if self.peer_id and self.peer.label:
                return self.peer.label
            return "Remote server"
        if self.provider_id and self.provider.display_name:
            return self.provider.display_name
        return "S3"

    def get_connection_url(self) -> str:
        from toto.vault.connection import BucketConnectionSpec
        return BucketConnectionSpec.from_bucket(self).to_url()

    def get_public_file_url(self, file_key: str) -> str:
        # A public base URL is a third-party origin — off with external buckets.
        if not self.public_base_url or not external_buckets_allowed():
            return ""
        base = self.public_base_url.rstrip("/")
        return f"{base}/{file_key}"


class BucketSecret(models.Model):
    """A bucket's storage credential, sealed under ``FIELD_ENCRYPTION_KEY``.

    The custody the owner chose on 2026-09-30 for S3 keys entered in
    Storage → Management: the same Fernet key that seals a BucketPeer's api
    key (``peering._fernet``). What that buys and what it does not, plainly:
    a database dump alone yields ciphertext; a dump PLUS the deploy config
    (which carries the key) yields the secret. That is weaker than the
    storage-PIN custody in ``credentials.py`` and stronger than a plaintext
    column, and it is what lets a background job purge a bucket with nobody
    at the keyboard.

    Never rendered, logged, put in JSON, a form draft or the admin — only
    ``hint`` (the last four characters of the access key id) is shown, so an
    operator can tell which key is live. The S3 driver receives the opened
    dict per use (``storage_backends.get_bucket_storage``) and the dict dies
    with the driver.
    """

    bucket = models.OneToOneField(Bucket, on_delete=models.CASCADE,
                                  related_name="sealed_secret")
    ciphertext = models.BinaryField(editable=False)
    hint = models.CharField(max_length=16, blank=True)
    sealed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bucket secret"
        verbose_name_plural = "Bucket secrets"

    def __str__(self):
        return f"Sealed credential of {self.bucket.name} (…{self.hint})"

    #: The only keys a sealed credential may carry — what the S3 driver reads.
    FIELDS = ("aws_access_key_id", "aws_secret_access_key", "session_token")

    def seal(self, secret: dict) -> None:
        """Encrypt ``secret`` into this row (the caller saves it).

        Refuses, with a sentence, on a host whose ``FIELD_ENCRYPTION_KEY`` is
        the per-process random fallback: the secret would be unreadable after
        the next restart, and nobody can type an S3 secret twice.
        """
        import json

        from .peering import _fernet

        if not field_key_configured():
            raise ValidationError(gettext(
                "This server has no permanent FIELD_ENCRYPTION_KEY, so a secret "
                "sealed now could not be opened after the next restart. Set "
                "FIELD_ENCRYPTION_KEY in the deploy configuration first."))
        clean = {k: str(v) for k, v in (secret or {}).items() if k in self.FIELDS and v}
        if not clean.get("aws_access_key_id") or not clean.get("aws_secret_access_key"):
            raise ValidationError(gettext("Both the access key id and the secret key are needed."))
        self.ciphertext = _fernet().encrypt(json.dumps(clean).encode())
        self.hint = clean["aws_access_key_id"][-4:]

    def open(self) -> dict:
        import json

        from .peering import _fernet

        return json.loads(_fernet().decrypt(bytes(self.ciphertext)).decode())


def personal_bucket(user):
    """The user's own ``personal-<username>`` bucket, created on first use.

    The one shape every door that files something "somewhere of theirs" uses
    (the vault API, the new-file picker, aralia's renders). Since
    owners are SET_NULL a bucket with that slug may belong to nobody — its
    account was deleted — or to somebody else (Management gave it away).
    Handing such a bucket to a NEW account that happens to carry the old
    username would give them the previous holder's files through the
    bucket-owner clause, so it is never reused: the next free
    ``personal-<username>-<n>`` is taken instead. A personal bucket that is
    being deleted is skipped the same way — nothing new lands in it.
    """
    from django.db import IntegrityError, transaction

    base_slug = f"personal-{user.username}"
    base_name = f"Personal — {user.username}"
    for n in range(1, 100):
        slug = base_slug if n == 1 else f"{base_slug}-{n}"
        name = base_name if n == 1 else f"{base_name} ({n})"
        found = Bucket.objects.filter(slug=slug).first()
        if found is not None:
            if found.owner_id == user.pk and not found.is_being_deleted:
                return found
            continue
        if Bucket.objects.filter(name=name).exists():
            continue
        try:
            with transaction.atomic():
                return Bucket.objects.create(owner=user, slug=slug, name=name,
                                             storage_backend=StorageBackend.LOCAL)
        except IntegrityError:
            # Two requests raced for the same slug: take theirs if it is ours.
            found = Bucket.objects.filter(slug=slug, owner=user).first()
            if found is not None and not found.is_being_deleted:
                return found
    raise RuntimeError(f"No free personal bucket slug for {user.username}.")


def personal_buckets_of(user):
    """``user``'s personal buckets, the shape :func:`personal_bucket` makes:
    owned by them, ``personal-<username>`` or ``personal-<username>-<n>``."""
    import re

    pattern = rf"^{re.escape(f'personal-{user.username}')}(-[0-9]+)?$"
    return Bucket.objects.filter(owner=user, slug__regex=pattern)


def forget_personal_buckets(user) -> int:
    """Rename ``user``'s personal buckets so that neither name nor address
    carries their username (2026-10-01, 37c.21) — ``erase_user`` calls this
    before the account goes. The buckets stay, ownerless, for the files other
    people keep in them, until a superuser gives them on or deletes them; they
    were "Personal — <username>" at ``personal-<username>`` for good.
    Returns how many were renamed."""
    renamed = 0
    for bucket in personal_buckets_of(user):
        name, slug = f"Personal — deleted account {bucket.pk}", f"deleted-{bucket.pk}"
        n = 1
        while (Bucket.objects.filter(name=name).exclude(pk=bucket.pk).exists()
               or Bucket.objects.filter(slug=slug).exclude(pk=bucket.pk).exists()):
            n += 1
            name = f"Personal — deleted account {bucket.pk} ({n})"
            slug = f"deleted-{bucket.pk}-{n}"
        Bucket.objects.filter(pk=bucket.pk).update(name=name, slug=slug)
        renamed += 1
    return renamed


class BucketClosed(RuntimeError):
    """The bucket is being deleted: no file may be added to it or moved into it.

    Raised by ``VaultFile.save`` — the backstop behind every door. The doors
    that take uploads refuse earlier, with the same sentence, before any byte
    is written (``storage_backends.persist_upload``).
    """


def closed_bucket_sentence(bucket) -> str:
    return gettext("The bucket '%(name)s' is being deleted — nothing new can be "
                   "added to it.") % {"name": getattr(bucket, "name", "") or "?"}


class FileOrigin(models.TextChoices):
    #: Uploaded or created here; this host holds (or S3-holds) the bytes.
    NATIVE = "native", "Native"
    #: A metadata stub maintained by the mirror refresh; the bytes live on the
    #: peer. Metadata edits are refused ("change it on the origin host") and
    #: deleting the row never deletes anything remote.
    MIRROR = "mirror", "Mirrored"


#: How long a trashed file waits before the nightly purge takes it (the
#: owner's decision, 2026-10-01). Read through :func:`trash_days`, the one
#: place pages and the purge ask.
DEFAULT_TRASH_DAYS = 30


def trash_days() -> int:
    """Days a trashed file is kept: ``settings.VAULT_TRASH_DAYS``, else 30."""
    try:
        days = int(getattr(settings, "VAULT_TRASH_DAYS", DEFAULT_TRASH_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_TRASH_DAYS
    return days if days > 0 else DEFAULT_TRASH_DAYS


class LiveFileManager(models.Manager):
    """``VaultFile.objects``: the files that are NOT in the trash.

    WHY THE DEFAULT MANAGER HIDES (2026-10-01). A trashed file must be absent
    from every read door — the listing, the JSON API, search, the peer
    manifest, the editors, previews, the zip builder and the many apps that
    read the vault. Filtering at each door means a door that forgets the
    filter LEAKS the file; filtering here means such a door HIDES it, which
    is the failure the owner can live with. Reverse relations
    (``bucket.files``, ``directory.files``) are built from this class, so
    they hide too.

    What must see trashed rows says so by name — ``VaultFile.all_objects``:
    the purge (a file and a bucket), the admin, the storage levy and the
    bucket usage figures (trashed bytes still count: the trash is not a free
    hiding place), and the trash itself. Forward foreign keys
    (``FileVersion.file``, a lock's file) resolve through ``all_objects``
    as the base manager, so a trashed file's versions still find it.
    """

    def get_queryset(self):
        return super().get_queryset().filter(trashed_at__isnull=True)


class VaultFile(models.Model):
    FILE_TYPES = [
        ('pdf', 'PDF'),
        ('image', 'Image'),
        ('html', 'HTML'),
        ('text', 'Text File'),
        ('markdown', 'Markdown'),
        ('json', 'JSON'),
        ('yaml', 'YAML'),
        ('xml', 'XML'),
        ('latex', 'LaTeX'),
        ('bib', 'Bibliography'),
        ('csv', 'CSV'),
        ('svg', 'SVG File'),
        ('audio', 'Audio'),
        ('video', 'Video'),
        ('python', 'Python'),
        ('neojson', 'NeoJSON'),
        ('sheet', 'Primula Sheet'),   # LEGACY: a Univer workbook as .json; a plain file since 2026-10-01
        # A sheet (2026-10-02): `.uson`, a Univer workbook in a small JSON
        # envelope, {"format": "uson", "version": 1, "workbook": {...}}. The
        # host's app plays and edits it (zenobia: toto.primula); without one
        # it is a plain file.
        ('uson', 'Sheet'),
        ('pxml', 'Presentation'),     # a slide deck (.pxml), authored in zinnia, shown by toto.memo
        ('presentation', 'Presentation'),  # LEGACY spelling of 'pxml' — see below
        ('zip', 'Archive'),
    ]
    # The retired doc types (notebook/.tpy, contract/.contract) are ordinary 'xml'
    # now, content-sniffed by mandragora/notarius. Existing rows keep their old
    # file_type string (choices aren't DB-enforced) and still open.
    #
    # WHY A DECK HAS ITS OWN CLASS AND ITS OWN EXTENSION.
    # The plugin registries are dict[key -> plugin] and `for_file_type` is
    # `registry.get(file_type)`, so a plugin only ever fires when its `key` equals
    # a file_type — and `key="xml"` is already taken by toto.editor, with
    # BasePlugin.register raising on a duplicate. So a deck needs a type of its
    # own to have a Play button at all.
    #
    # It also needs an EXTENSION of its own. While decks were typed by class but
    # named `.xml`, nothing could tell a deck from a cyprian document or a
    # notebook without reading the bytes — so toto.memo content-sniffed up to 300
    # files on every gallery visit and retyped rows behind the user's back. A
    # `.pxml` file says what it is in its name, which is what `_EXT_MAP` below
    # turns into a type at every ingest door, and the sniffing is gone.
    #
    # 'presentation' is the LEGACY spelling of this same class, kept in the list
    # on purpose. Migration 0021 retypes local rows, but it cannot reach every
    # one: mirrored stubs are re-stamped from the peer on each refresh, rows in
    # s3/remote buckets are unreadable from a migration, and an ENCRYPTED deck
    # typed 'xml' cannot be identified at all. Dropping the string would make
    # those rows unnameable in admin and rejected by RenameFileView. toto.memo
    # reads both spellings.
    #
    # 'ctml' and 'document' are BOTH gone as of migration 0024. The writer's
    # own format was retired on 2026-08-29 and a written document is an
    # ordinary 'html' file now — see toto/cyprian/htmldoc.py for what the
    # container carried and why none of it was missed. 0023 is left untouched
    # because it is deployed; 0024 is the forward migration that undoes it.
    # The historical note kept below is what 0023 did and why:
    # a Cyprian document was an `.xml` file typed 'document' —
    # a name that said nothing, on an extension toto.editor already owned — so
    # what a file WAS depended on which editor had touched it last. It became
    # CTML, with its own extension. Migration 0023 retyped what it could reach and
    # cannot reach the same three populations 0021 could not, so the old string
    # stays nameable here forever. Cyprian reads both spellings.
    #
    # The '.pml' extension of the first deck era stays retired and is NOT in
    # _EXT_MAP; it was never the same format's current spelling.

    _EXT_MAP = {
        ".tex": "latex", ".sty": "latex", ".cls": "latex", ".dtx": "latex", ".ins": "latex",
        ".bib": "bib",
        ".pdf": "pdf",
        ".svg": "svg",
        ".csv": "csv",
        ".json": "json",
        # A sheet says what it is in its name (2026-10-02), so no JSON file
        # is ever sniffed for a workbook.
        ".uson": "uson",
        ".neojson": "neojson",
        ".yaml": "yaml", ".yml": "yaml",
        # Before ".xml" is irrelevant (dict lookup, not a scan) but the pairing
        # is the point: a deck is ".pxml", everything else XML-shaped — cyprian
        # documents, notebooks, contracts — stays ".xml".
        ".pxml": "pxml",
        # `.ctml` is deliberately ABSENT. A written document is `.html` now, so
        # it needs no entry of its own — the `.html` line below already carries
        # it, and that is the point of retiring the format: one extension, one
        # type, one editor door.
        ".xml": "xml",
        ".html": "html", ".htm": "html",
        ".md": "markdown", ".markdown": "markdown",
        ".txt": "text", ".rst": "text",
        ".py": "python",
        ".mp3": "audio", ".ogg": "audio", ".wav": "audio", ".flac": "audio", ".aac": "audio",
        ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video", ".webm": "video",
        ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
        ".webp": "image", ".bmp": "image", ".tiff": "image",
        ".zip": "zip",
    }

    @classmethod
    def detect_type(cls, mime: str, filename: str = "") -> str:
        # Extension-first for types browsers mis-label as text/plain
        if filename:
            ext = os.path.splitext(filename)[1].lower()
            if ext in cls._EXT_MAP:
                return cls._EXT_MAP[ext]
        if not mime:
            return "text"
        mime = mime.lower()
        if "pdf" in mime:
            return "pdf"
        if mime == "image/svg+xml":
            return "svg"
        if mime.startswith("image/"):
            return "image"
        if "html" in mime:
            return "html"
        if "neojson" in mime:
            return "neojson"
        if "json" in mime:
            return "json"
        if "yaml" in mime:
            return "yaml"
        if "xml" in mime:
            return "xml"
        if "latex" in mime or mime in ("application/x-tex", "application/x-latex", "text/x-tex"):
            return "latex"
        if "bibtex" in mime:
            return "bib"
        if "csv" in mime:
            return "csv"
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"
        if "zip" in mime:  # application/zip, application/x-zip-compressed
            return "zip"
        return "text"

    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    key = models.SlugField(max_length=255, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    # Private storage, not the default one: MEDIA_ROOT is web-served and this is
    # not web-servable. Same location, so no file moves; no base_url, so nothing
    # can mint a link to it. See toto/vault/storage.py.
    file = models.FileField(upload_to='vault/files/', storage=private_storage)
    file_type = models.CharField(max_length=16, choices=FILE_TYPES)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_encrypted = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False, help_text="If true, file is visible to others")
    notes = models.TextField(blank=True, null=True)
    file_size_bytes = models.PositiveBigIntegerField(
        default=0,
        help_text="File size in bytes, captured at upload time.",
    )
    origin = models.CharField(
        max_length=8, choices=FileOrigin.choices,
        default=FileOrigin.NATIVE, db_index=True,
        help_text="Mirrored rows are the peer's listing, not this host's "
                  "bytes; their metadata is changed on the origin host.",
    )
    #: PROTECT since 2026-09-30: a bucket is deleted only by the Management
    #: purge, which takes every file (row and bytes) first. SET_NULL used to
    #: leave the bytes behind AND drop the files out of their bucket's
    #: clearance keeping — a file in no bucket is read by its own rules, so a
    #: deleted kept bucket opened its files. Nothing may end with bucket=None
    #: by deleting its bucket.
    bucket = models.ForeignKey(Bucket, on_delete=models.PROTECT, null=True, blank=True, related_name='files')
    directory = models.ForeignKey(
        'VaultDirectory', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='files'
    )
    #: The trash (2026-10-01). A trashed file keeps its bytes, its versions
    #: and its bucket (so the bucket's clearance still keeps it and its bytes
    #: still count); it leaves its folder — ``directory`` is cleared and the
    #: folder remembered in ``trashed_from`` for the restore. ``related_name``
    #: '+' on both: a reverse manager would be built from the hiding default
    #: manager and answer nothing anyway.
    trashed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    trashed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    trashed_from = models.ForeignKey(
        'VaultDirectory', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+')

    objects = LiveFileManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = "Vault File"
        verbose_name_plural = "Vault Files"
        base_manager_name = "all_objects"
        # One LIVE file per key in a bucket (2026-10-01): a trashed file keeps
        # its key for the restore, and a new upload of the same name must
        # still work — so the rule ignores the trash, and the restore finds a
        # free key when the old one was taken meanwhile.
        constraints = [
            models.UniqueConstraint(
                fields=["bucket", "key"], condition=models.Q(trashed_at__isnull=True),
                name="vault_one_live_file_per_key"),
        ]

    def __str__(self):
        return f"{self.title} ({self.owner.username})"

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # What the row said when read, so save() can tell a file MOVED into a
        # bucket from one that was always there.
        instance._loaded_bucket_id = instance.__dict__.get("bucket_id")
        return instance

    def _refuse_closed_bucket(self):
        """Nothing new lands in a bucket that is being deleted (2026-09-30):
        the background purge takes every file and then the bucket, and a file
        arriving mid-purge would either block the final delete (PROTECT) or
        vanish with it. Only a file that is NEW here is checked — a file
        already in the bucket may still be saved (a scan stamp, a rename)."""
        if not self.bucket_id:
            return
        arriving = self._state.adding or (
            getattr(self, "_loaded_bucket_id", self.bucket_id) != self.bucket_id)
        if not arriving:
            return
        closed = (Bucket.objects.filter(pk=self.bucket_id, deletion_requested_at__isnull=False)
                  .only("name").first())
        if closed is not None:
            raise BucketClosed(closed_bucket_sentence(closed))

    def save(self, *args, **kwargs):
        self._refuse_closed_bucket()
        if self.file and not self.key:
            base_name = os.path.splitext(os.path.basename(self.file.name))[0]
            candidate_key = slugify(base_name)
            if VaultFile.objects.filter(bucket=self.bucket, key=candidate_key).exists():
                raise ValueError(f"A file with key '{candidate_key}' already exists in this bucket.")
            self.key = candidate_key

        if self.file and not self.file_size_bytes:
            try:
                self.file_size_bytes = self.file.size
            except Exception:
                pass

        super().save(*args, **kwargs)
        self._loaded_bucket_id = self.bucket_id

    @property
    def can_be_trashed(self) -> bool:
        """False where the trash cannot hold the bytes (2026-10-01): a row in
        a mounted remote bucket, or a mirror stub, names the PEER's file —
        this host cannot keep it for a restore — so those doors keep their
        immediate delete."""
        if getattr(self, "origin", "") == FileOrigin.MIRROR:
            return False
        bucket = self.bucket if self.bucket_id else None
        return not (bucket is not None
                    and bucket.storage_backend == StorageBackend.REMOTE_TOTO)

    def trash(self, by=None) -> None:
        """Move this file to the trash: hidden from every door that reads
        ``VaultFile.objects``, bytes and versions kept, its folder remembered
        for the restore. Idempotent — a file already in the trash keeps its
        first stamp."""
        if self.trashed_at is not None:
            return
        from django.utils import timezone

        self.trashed_at = timezone.now()
        self.trashed_by = by if getattr(by, "pk", None) else None
        self.trashed_from = self.directory
        self.directory = None
        self.save(update_fields=["trashed_at", "trashed_by", "trashed_from", "directory"])

    def create_hash(self):
        """sha256 of the content, from wherever the bucket keeps it, or None.

        Through the bucket driver rather than the FieldFile: the FieldFile
        only ever opens local disk, and hashing an s3-backed file through it
        answered None on every host that could not see the bytes.
        """
        if not self.file:
            return None
        try:
            from toto.vault.storage_backends import read_file_bytes

            return hashlib.sha256(read_file_bytes(self)).hexdigest()
        except Exception:
            return None

    def get_file_info(self):
        return {
            "title": self.title,
            "owner": self.owner.username,
            "type": self.file_type,
            "encrypted": self.is_encrypted,
            "public": self.is_public,
            "uploaded": self.uploaded_at.strftime("%Y-%m-%d"),
            "notes": self.notes or "—"
        }

    def get_strategy(self):
        if self.file_type == 'pdf':
            return PdfStrategy()
        if self.file_type == 'image':
            return ImageStrategy()
        return TextStrategy()

    def encrypt(self, password: str, owner_password=None):
        if self.is_encrypted:
            return
        strategy = self.get_strategy()
        if self.file_type == 'pdf' and not owner_password:
            owner_password = password
        strategy.encrypt(self, password=password, owner_password=owner_password)
        # Imported here, not at the top: signals.py imports this module.
        from .signals import file_encrypted

        file_encrypted.send_robust(sender=VaultFile, file=self)

    def decrypt(self, password: str):
        if not self.is_encrypted:
            raise ValueError("File is not encrypted.")
        strategy = self.get_strategy()
        strategy.decrypt(self, password=password)

    def get_public_url(self):
        if not self.key:
            return None
        if not self.bucket:
            return None
        try:
            return reverse('vault:public_file', args=[self.bucket.slug, self.key])
        except Exception:
            return None


class BucketClearance(models.Model):
    """One clearance keeping one bucket (2026-09-30).

    Clearances go on groups, never on items: a vault file is kept by its
    BUCKET. A file in a bucket with rows here is read by superusers and by
    holders of one of the bucket's clearances — nobody else: not its owner,
    not the public flag, not the bucket's owner, not a folder's ACL
    (``access.may_read`` / ``access.gate_by_bucket``, through
    ``socialhub.clearance_access``). A file in a bucket with none — or in no
    bucket — is what it always was. The clearance is PROTECTED: a clearance
    still keeping a bucket cannot be deleted, which would open its files.
    Only a superuser on the Superuser plan sets a bucket's clearances
    (``clearances.bucket_clearances``).
    """

    bucket = models.ForeignKey(Bucket, on_delete=models.CASCADE, related_name="clearance_rows")
    clearance = models.ForeignKey("socialhub.Clearance", on_delete=models.PROTECT,
                                  related_name="bucket_rows")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bucket", "clearance"],
                                    name="vault_bucket_clearance_once"),
        ]

    def __str__(self):
        return f"{self.bucket.name} — {self.clearance.name}"


class FileGateway(models.Model):
    """
    A user-facing upload gateway tied to exactly one directory.
    One gateway per directory; root-level uploads are not allowed via gateway.
    """

    name = models.CharField(max_length=200)

    directory = models.OneToOneField(
        'VaultDirectory',
        on_delete=models.CASCADE,
        related_name='gateway',
    )

    bucket = models.ForeignKey(
        Bucket,
        on_delete=models.CASCADE,
        related_name='gateways',
    )

    allowed_users = models.ManyToManyField(User, blank=True)

    description = models.TextField(blank=True, null=True)

    make_public = models.BooleanField(
        default=False,
        help_text="If enabled, all files uploaded through this gateway become public.",
    )

    max_file_size = models.PositiveIntegerField(
        default=10 * 1024,
        help_text="Maximum allowed file size in KB.",
    )

    class Meta:
        verbose_name = "File Gateway"
        verbose_name_plural = "File Gateways"

    def __str__(self):
        return f"Gateway → {self.directory}"

    def save(self, *args, **kwargs):
        if self.directory_id:
            self.bucket_id = (
                VaultDirectory.objects
                .filter(pk=self.directory_id)
                .values_list('bucket_id', flat=True)
                .first()
            )
        super().save(*args, **kwargs)


class BucketCopyLog(models.Model):
    from_bucket = models.ForeignKey(
        Bucket, on_delete=models.SET_NULL, null=True, related_name="copies_out"
    )
    to_bucket = models.ForeignKey(
        Bucket, on_delete=models.SET_NULL, null=True, related_name="copies_in"
    )
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    file_count = models.PositiveIntegerField(default=1)
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Bucket Copy Log"
        verbose_name_plural = "Bucket Copy Logs"
        ordering = ["-performed_at"]

    def __str__(self):
        return f"{self.from_bucket} → {self.to_bucket} ({self.file_count} files)"


class VaultDirectory(models.Model):
    """
    A named folder inside a Bucket. May be nested (parent → subdirectories).
    Access is restricted to allowed_users when the whitelist is non-empty.
    """

    name = models.CharField(max_length=200)
    bucket = models.ForeignKey(Bucket, on_delete=models.CASCADE, related_name='directories')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_directories')
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, related_name='subdirectories'
    )
    allowed_users = models.ManyToManyField(
        User, blank=True, related_name='accessible_directories',
        help_text="Leave empty to allow all authenticated users."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vault Directory"
        verbose_name_plural = "Vault Directories"
        unique_together = ('bucket', 'parent', 'name')

    def __str__(self):
        return self.full_path()

    def full_path(self):
        parts = []
        node = self
        while node is not None:
            parts.append(node.name)
            node = node.parent
        return "/".join(reversed(parts))

    def breadcrumb(self):
        crumbs = []
        node = self
        while node is not None:
            crumbs.append(node)
            node = node.parent
        return list(reversed(crumbs))

    def user_can_access(self, user):
        if not user or not user.is_authenticated:
            return not self.allowed_users.exists()
        if user.is_superuser:
            return True
        if not self.allowed_users.exists():
            return True
        return self.allowed_users.filter(pk=user.pk).exists()



# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------
# Editors (cyprian, memo, primula) autosave straight onto the live VaultFile.
# A VERSION is something else: a deliberate act by a person who decided this
# state was worth keeping and gave it a name. That distinction is the whole
# design — versions number in the handful, not the hundreds, which is what makes
# a plain table sufficient where a delta-compressing engine would otherwise be
# needed.
#
# Lives in vault rather than in the three editors because a version of a vault
# file is a vault concept: vault owns the storage backends, the access rules and
# the bytes. Putting it here also makes every file type versionable rather than
# only those three.


def version_blob_path(instance, filename):
    """``vault/versions/ab/abcdef…`` — the digest IS the address.

    Fanned out by the first two hex characters so no single directory holds
    every blob a busy host ever wrote; the same shape git uses for loose
    objects, and for the same reason.
    """
    digest = instance.content_hash or "unknown"
    return f"vault/versions/{digest[:2]}/{digest}"


class VersionBlob(models.Model):
    """One immutable body, stored once no matter how many versions cite it.

    The repo's first content-addressed store. Everything else that hashes bytes
    here — ``VaultFile.content_hash``, the backup manifest, the ledger chain —
    keeps the digest *beside* a path-addressed object; this keys the object BY
    the digest, which is what makes dedupe possible at all.

    What that buys, concretely: restoring v3 and saving again creates a new
    version pointing at v3's existing blob and writes no bytes. Saving twice
    without changing anything is free. Documents here carry base64 images inline
    by design, so an unchanged 8 MB illustration is exactly the thing that must
    not be stored twice.
    """

    content_hash = models.CharField(
        max_length=64, unique=True,
        help_text="sha256 of the bytes. Unique — this is the dedupe.")
    data = models.FileField(upload_to=version_blob_path)
    size_bytes = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Version blob"
        verbose_name_plural = "Version blobs"

    def __str__(self):
        return f"{self.content_hash[:12]}… ({self.size_bytes} B)"

    def save(self, *args, **kwargs):
        # Immutable by construction: a blob's identity IS its content, so an
        # edited blob would be a different blob. Guarding here rather than
        # trusting callers, the LedgerTransaction idiom.
        if self.pk is not None:
            raise ValidationError(
                "A version blob is addressed by its own digest and cannot be "
                "edited — write a new one.")
        super().save(*args, **kwargs)



class FileLock(models.Model):
    """Who is editing this file right now. One holder, enforced by the database.

    The FIRST of three layers. This one prevents the collision; the editors'
    ``base_hash`` check detects the ones that slip past it (an expired lock, two
    tabs of one person); and a conflicting draft rescues whatever the first two
    missed. Each layer is allowed to fail because the next one catches it —
    the same arrangement the mint chain uses for its single head.

    ``OneToOneField`` is the guarantee, not the service: two people cannot both
    hold a file because the database will not store two rows for it.

    HARD. A non-holder gets the document read-only and their save is refused
    with 423 by the server, not merely hidden by the template. A lock only the
    UI respects is advice.

    SHORT-LIVED. The open editor refreshes ``expires_at`` on a heartbeat; a
    closed laptop therefore frees the document within a couple of minutes and
    nobody ever has to break a lock by hand. Expiry is checked on read rather
    than swept, so a stale row is simply not a lock.

    Not to be confused with the "edit lock" in vault/tests.py, which is about
    encrypted files being barred from editors entirely.
    """

    file = models.OneToOneField(VaultFile, on_delete=models.CASCADE,
                                related_name="edit_lock")
    holder = models.ForeignKey(User, on_delete=models.CASCADE,
                               related_name="held_file_locks")
    acquired_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "File editing lock"
        verbose_name_plural = "File editing locks"

    def __str__(self):
        return f"{self.file.title} held by {self.holder}"

    @property
    def is_live(self) -> bool:
        from django.utils import timezone

        return self.expires_at > timezone.now()


class FileVersion(models.Model):
    """One saved state of a vault file, and who decided to save it.

    ONE LINEAR HISTORY PER FILE. There is no branching and no merge: the three
    editors' formats are not mergeable (cyprian's whole body is a single CDATA
    line, primula's whole workbook is a single JSON line), and every product
    that ships rich-document editing answers concurrency with either real-time
    collaboration or a conflicting copy — never a merge UI. ``author`` is
    attribution, not a branch.

    APPEND-ONLY. Restoring v3 writes the live file and records v8 "restored
    from v3"; it never rewinds ``number``. History is not rewritten here for the
    same reason it is not rewritten in the ledger or the mint chain.
    """

    file = models.ForeignKey(VaultFile, on_delete=models.CASCADE,
                             related_name="versions")
    #: What the user sees: v1, v2, v3. Per file, monotonic, never reused.
    number = models.PositiveIntegerField()
    blob = models.ForeignKey(VersionBlob, on_delete=models.PROTECT,
                             related_name="versions")
    #: A name the author gave it. A LABELLED version is never auto-pruned — it
    #: was a conscious act, and silently deleting it would break the very idea
    #: the feature rests on.
    label = models.CharField(max_length=200, blank=True)
    author = models.ForeignKey(User, null=True, blank=True,
                               on_delete=models.SET_NULL,
                               related_name="file_versions")
    #: True when this body LOST an optimistic-concurrency check and was kept
    #: rather than thrown away. Exempt from pruning like any labelled version.
    is_conflict = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        verbose_name = "File version"
        verbose_name_plural = "File versions"
        constraints = [
            # Two versions numbered v4 on one file would make "restore v4"
            # ambiguous, which is the one thing the UI must never be.
            models.UniqueConstraint(fields=["file", "number"],
                                    name="vault_one_version_per_number"),
        ]
        indexes = [models.Index(fields=["file", "-number"],
                                name="vault_version_file_idx")]

    def __str__(self):
        return f"{self.file.title} v{self.number}"

    @property
    def is_pinned(self) -> bool:
        """Never auto-pruned: somebody named it, or it is rescued work."""
        return bool(self.label) or self.is_conflict

    def read(self) -> bytes:
        with self.blob.data.open("rb") as handle:
            return handle.read()

# ---------------------------------------------------------------------------
# Usage metering
# ---------------------------------------------------------------------------
# Vault owns its own quota tables rather than sharing a central pair — see
# toto.quota.models for why. Metrics: storage.request (one upload) and
# storage.transfer_mb (its size).

class VaultUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Vault usage event"
        verbose_name_plural = "Vault usage events"


class VaultQuotaPolicy(AbstractQuotaPolicy):
    events = VaultUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Vault quota policy"
        verbose_name_plural = "Vault quota policies"


# Imported last so the peering and mirror models are part of this app's
# migration state. See each module's docstring for the doctrines they carry.
from .peering import (  # noqa: E402,F401
    BUCKET_RIGHTS,
    BucketGrant,
    BucketPeer,
    has_bucket_right,
)
from .mirror import (  # noqa: E402,F401
    BucketRefreshRun,
    RefreshStatus,
)
from .transfer import (  # noqa: E402,F401
    CopyPolicy,
    TransferRun,
    TransferStatus,
)
from .credentials import (  # noqa: E402,F401
    CredentialEnrollment,
    CredentialWrap,
    RemoteCredential,
    RunCapability,
)
