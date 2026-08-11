import base64
import hashlib
import uuid as uuid_lib
from decimal import Decimal, ROUND_DOWN

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


# ---------------------------------------------------------------------------
# Amount conversion helpers
# ---------------------------------------------------------------------------

def to_base_units(display_amount: Decimal, decimals: int) -> int:
    factor = Decimal(10) ** decimals
    return int((Decimal(str(display_amount)) * factor).to_integral_value())


def from_base_units(base_units: int, decimals: int) -> Decimal:
    factor = Decimal(10) ** decimals
    return (Decimal(base_units) / factor).quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN)


# ---------------------------------------------------------------------------
# Monetary issuer
# ---------------------------------------------------------------------------

class IssuerStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    RETIRED = "retired", "Retired"


class CurrencyIssuerManager(models.Manager):
    def create_local(self, *, label: str):
        """Mint this host's own issuer keypair.

        The private half is sealed under MONETARY_ISSUER_KEY — a secret
        deliberately separate from FIELD_ENCRYPTION_KEY, because assets travel
        in backups and that key travels in the deploy config. See toto/assets/
        issuer.py for the full argument.
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)

        from .issuer import _issuer_fernet, fingerprint_for

        fernet = _issuer_fernet()      # before generating: fail closed, not half-done
        key = Ed25519PrivateKey.generate()
        private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()

        return self.create(
            label=label,
            fingerprint=fingerprint_for(public_pem),
            public_key_pem=public_pem,
            private_key_encrypted=fernet.encrypt(private_pem),
            is_self=True)


class CurrencyIssuer(models.Model):
    """A monetary authority — normally exactly one row, describing this host.

    A branch has this table and no local row in it, or a row for the MASTER
    carrying only a public key: that is the pinned trust anchor it verifies
    genesis documents against. Holding a public key lets you check; only the
    private half lets you issue.
    """

    label = models.CharField(max_length=200)
    #: SHA-256 of the public key PEM. Travels inside every genesis document,
    #: so it must derive from the public half alone.
    fingerprint = models.CharField(max_length=64, unique=True, editable=False)
    public_key_pem = models.TextField()
    private_key_encrypted = models.BinaryField(blank=True, null=True)
    #: True on the host this issuer IS. A branch pins its master with False.
    is_self = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=IssuerStatus.choices,
                              default=IssuerStatus.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CurrencyIssuerManager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Two local issuers would be two monetary authorities on one host.
            models.UniqueConstraint(
                fields=["is_self"], condition=models.Q(is_self=True),
                name="assets_one_local_issuer"),
        ]

    def __str__(self):
        return f"{self.label} ({self.fingerprint[:12]}…)"

    def private_key(self):
        """The Ed25519 private key, or raise. Only the master can do this."""
        from cryptography.hazmat.primitives import serialization

        from .issuer import NotTheMaster, _issuer_fernet

        if not self.private_key_encrypted:
            raise NotTheMaster(
                f"Issuer {self.fingerprint[:12]}… holds no private key here — "
                "it is a pinned remote authority, not this host.")
        raw = _issuer_fernet().decrypt(bytes(self.private_key_encrypted))
        return serialization.load_pem_private_key(raw, password=None)

    def sign_genesis(self, document: dict) -> str:
        from .currency_hash import sign_genesis

        return sign_genesis(self.private_key(), document)

    def verify_genesis(self, document: dict, signature: str) -> bool:
        from .currency_hash import verify_genesis_document

        return verify_genesis_document(self.public_key_pem, document, signature)


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

class AccountType(models.TextChoices):
    USER = "user", "User"
    SYSTEM = "system", "System"
    RESERVE = "reserve", "Reserve"
    EXTERNAL = "external", "External"


class TransactionType(models.TextChoices):
    ASSET_CREATE = "asset_create", "Asset Create"
    ASSET_TRANSFER = "asset_transfer", "Asset Transfer"
    REVERSAL = "reversal", "Reversal"
    ADJUSTMENT = "adjustment", "Adjustment"


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

class Asset(models.Model):
    name = models.CharField(max_length=255)
    unit_name = models.CharField(max_length=20, unique=True)
    decimals = models.PositiveSmallIntegerField()
    total_supply_base_units = models.PositiveBigIntegerField()
    active = models.BooleanField(default=True)
    is_currency = models.BooleanField(default=False, help_text="Accepted as a payment currency on the platform")
    backing_document = models.TextField(blank=True, help_text="What this asset is backed by (e.g. reserves held by …)")
    minting_authority = models.CharField(max_length=255, blank=True, help_text="Entity authorised to mint this asset")
    reserve_account = models.ForeignKey(
        "LedgerAccount",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="reserve_assets",
        help_text="Admin-controlled account that holds unminted supply and fulfils purchases.",
    )
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit_name})"

    def clean(self):
        if self.decimals is not None and self.decimals > 19:
            raise ValidationError({"decimals": "Decimals cannot exceed 19."})

    @property
    def total_supply_display(self) -> Decimal:
        return from_base_units(self.total_supply_base_units, self.decimals)


# ---------------------------------------------------------------------------
# LedgerAccount
# ---------------------------------------------------------------------------

class LedgerAccount(models.Model):
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    active = models.BooleanField(default=True)
    user_priority = models.IntegerField(
        default=0,
        help_text="Billing priority for this user's accounts. Higher = checked first.",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ledger_accounts",
    )
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


# ---------------------------------------------------------------------------
# AssetHolding
# ---------------------------------------------------------------------------

class AssetHolding(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="holdings")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="holdings")
    balance_base_units = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("asset", "account")]
        ordering = ["-balance_base_units"]

    def __str__(self):
        return f"{self.account.code} / {self.asset.unit_name}: {self.balance_base_units}"

    def clean(self):
        if self.balance_base_units is not None and self.balance_base_units < 0:
            raise ValidationError({"balance_base_units": "Balance cannot be negative."})

    @property
    def balance_display(self) -> Decimal:
        return from_base_units(self.balance_base_units, self.asset.decimals)


# ---------------------------------------------------------------------------
# LedgerAccountKey (forward-declared; full definition after LedgerAuthorization)
# ---------------------------------------------------------------------------

class LedgerAccountKeyState(models.TextChoices):
    ACTIVE      = "active",      "Active"
    SUSPENDED   = "suspended",   "Suspended"
    RETIRED     = "retired",     "Retired"
    COMPROMISED = "compromised", "Compromised"
    DESTROYED   = "destroyed",   "Destroyed"


class LedgerAccountKey(models.Model):
    """
    Binding between a LedgerAccount and an EncryptedPrivateKey in Gervazy.
    The account owns its keys; keys never delegate ownership — only sign.
    public_key_pem is a snapshot that must match the Gervazy EPK at creation time.
    """
    ledger_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="signing_keys",
    )
    encrypted_private_key = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        on_delete=models.PROTECT,
        related_name="ledger_account_keys",
    )
    key_id = models.CharField(max_length=100, unique=True)
    public_key_pem = models.TextField(
        help_text="Plaintext snapshot of the public key PEM. Must match gervazy.EncryptedPrivateKey.public_key_pem.",
    )
    algorithm = models.CharField(max_length=32, default="Ed25519")
    state = models.CharField(
        max_length=20,
        choices=LedgerAccountKeyState.choices,
        default=LedgerAccountKeyState.ACTIVE,
    )
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["ledger_account", "state"]),
            models.Index(fields=["key_id"]),
        ]

    def __str__(self):
        return f"LedgerAccountKey({self.key_id}, {self.ledger_account.code}, {self.state})"

    @property
    def is_active(self) -> bool:
        from django.utils import timezone
        if self.state != LedgerAccountKeyState.ACTIVE:
            return False
        now = timezone.now()
        if now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True

    def clean(self):
        if self.encrypted_private_key_id and self.public_key_pem:
            epk = self.encrypted_private_key
            if epk.public_key_pem.strip() != self.public_key_pem.strip():
                raise ValidationError({
                    "public_key_pem": "public_key_pem does not match gervazy.EncryptedPrivateKey.public_key_pem."
                })


# ---------------------------------------------------------------------------
# LedgerAuthorization
# ---------------------------------------------------------------------------

class LedgerAuthorization(models.Model):
    """
    Scoped delegation: a LedgerAccount grants a user or key the right to sign
    within explicit limits (scopes, amount, asset, time window).
    An authorization never owns assets and never acts as the account itself.
    """
    ledger_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="authorizations",
    )
    delegate_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="delegated_authorizations",
    )
    delegate_key = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="delegated_authorizations",
    )
    scopes = models.JSONField(
        default=list,
        help_text='List of allowed scope strings, e.g. ["transfer", "sign"].',
    )
    max_amount_base_units = models.BigIntegerField(
        null=True, blank=True,
        help_text="Per-operation ceiling. None = unlimited.",
    )
    asset = models.ForeignKey(
        Asset,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="authorizations",
        help_text="If set, delegation is restricted to this asset only.",
    )
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    signed_grant_payload = models.JSONField(default=dict)
    grant_signature = models.TextField(blank=True)
    signed_by_account_key = models.ForeignKey(
        LedgerAccountKey,
        on_delete=models.PROTECT,
        related_name="signed_authorizations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        delegate = self.delegate_user or self.delegate_key_id or "?"
        return f"LedgerAuthorization({self.ledger_account.code} → {delegate})"

    def is_valid_now(self) -> bool:
        from django.utils import timezone
        if self.revoked_at:
            return False
        now = timezone.now()
        if now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        if not self.signed_by_account_key.is_active:
            return False
        return True

    def allows_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])

    def allows_amount(self, asset_obj, amount_base_units: int) -> bool:
        if self.asset_id and self.asset_id != asset_obj.pk:
            return False
        if self.max_amount_base_units is not None and amount_base_units > self.max_amount_base_units:
            return False
        return True


# ---------------------------------------------------------------------------
# LedgerTransaction
# ---------------------------------------------------------------------------

class LedgerTransaction(models.Model):
    reference = models.CharField(max_length=255, unique=True)
    # ── Global identity (cross-platform) ──────────────────────────────────
    # Host-local integer ids mean two platforms' ledgers cannot be compared;
    # the uuid is the portable name of this transaction everywhere. On a row
    # applied FROM a peer, origin_platform names that peer and origin_uuid the
    # peer-side transaction this one mirrors or settles; both blank/null on
    # ordinary local activity. The local hash chain stays host-local by design
    # — cross-platform verifiability lives in the clearing checkpoints.
    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False, db_index=True)
    origin_platform = models.CharField(max_length=100, blank=True, default="")
    origin_uuid = models.UUIDField(null=True, blank=True)
    transaction_type = models.CharField(max_length=30, choices=TransactionType.choices)
    description = models.TextField(blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions")
    posted = models.BooleanField(default=False)
    reversed_transaction = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversal_set",
    )
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # ── Cryptographic signing fields (all optional; backwards-compatible) ──
    signed_by_key = models.ForeignKey(
        LedgerAccountKey,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="signed_transactions",
    )
    authorization = models.ForeignKey(
        LedgerAuthorization,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="signed_transactions",
    )
    payload_hash = models.CharField(max_length=128, blank=True)
    signature = models.TextField(blank=True)
    nonce = models.CharField(max_length=128, blank=True)
    idempotency_key = models.CharField(max_length=128, null=True, blank=True, unique=True)
    signed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} ({self.get_transaction_type_display()})"

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                original = LedgerTransaction.objects.get(pk=self.pk)
            except LedgerTransaction.DoesNotExist:
                original = None
            if original and original.posted:
                raise ValidationError("Posted transactions are immutable.")
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# LedgerEntry
# ---------------------------------------------------------------------------

class LedgerEntry(models.Model):
    transaction = models.ForeignKey(LedgerTransaction, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="entries")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="entries")
    amount_base_units = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "ledger entries"

    def __str__(self):
        sign = "+" if self.amount_base_units >= 0 else ""
        return f"{self.account.code} {sign}{self.amount_base_units} {self.asset.unit_name}"

    def clean(self):
        if self.amount_base_units == 0:
            raise ValidationError({"amount_base_units": "Amount cannot be zero."})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Ledger entries are immutable after creation.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries cannot be deleted.")

    @property
    def amount_display(self) -> Decimal:
        return from_base_units(self.amount_base_units, self.asset.decimals)


# ---------------------------------------------------------------------------
# LedgerHash
# ---------------------------------------------------------------------------

class LedgerHash(models.Model):
    transaction = models.OneToOneField(
        LedgerTransaction,
        on_delete=models.PROTECT,
        related_name="hash_record",
    )
    previous_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "ledger hash"
        verbose_name_plural = "ledger hashes"

    def __str__(self):
        return f"{self.transaction.reference}: {self.hash[:16]}…"


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

class Currency(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=5, blank=True)
    asset = models.OneToOneField(
        'Asset',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='currency',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'currencies'
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.name}"


# ---------------------------------------------------------------------------
# WalletAuthorization
# ---------------------------------------------------------------------------

def _fernet_key() -> bytes:
    """Derive a 32-byte Fernet key from Django's SECRET_KEY."""
    raw = settings.SECRET_KEY.encode()
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


class WalletAuthorization(models.Model):
    """
    A named keypair that authorizes automated transactions on a LedgerAccount.
    When a StorageAccount has an associated WalletAuthorization, vault billing
    can sign operations without interactive PIN confirmation.
    The private key is encrypted at rest with a key derived from SECRET_KEY.
    """
    name = models.CharField(max_length=255)
    ledger_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.CASCADE,
        related_name="wallet_authorizations",
    )
    public_key = models.TextField(help_text="Public key (PEM or hex).")
    private_key_encrypted = models.TextField(
        blank=True,
        help_text="Private key encrypted with the server master secret. Do not expose.",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Wallet Authorization"
        verbose_name_plural = "Wallet Authorizations"

    def __str__(self):
        return f"{self.name} ({self.ledger_account.code})"

    def set_private_key(self, raw_private_key: str) -> None:
        from cryptography.fernet import Fernet
        f = Fernet(_fernet_key())
        self.private_key_encrypted = f.encrypt(raw_private_key.encode()).decode()

    def get_private_key(self) -> str:
        from cryptography.fernet import Fernet
        f = Fernet(_fernet_key())
        return f.decrypt(self.private_key_encrypted.encode()).decode()


class WalletPin(models.Model):
    """Per-user wallet PIN stored as a Gervazy EncryptedSecret."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wallet_pin',
    )
    secret = models.OneToOneField(
        'gervazy.EncryptedSecret',
        on_delete=models.CASCADE,
        related_name='wallet_pin',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"WalletPin({self.user})"



# Assets meters its own expensive read — the chain verification.

class AssetsUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Assets usage event"
        verbose_name_plural = "Assets usage events"


class AssetsQuotaPolicy(AbstractQuotaPolicy):
    events = AssetsUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Assets quota policy"
        verbose_name_plural = "Assets quota policies"
