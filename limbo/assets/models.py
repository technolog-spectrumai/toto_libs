import base64
import hashlib
import uuid as _uuid
from decimal import Decimal, ROUND_DOWN

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


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
    is_currency = models.BooleanField(default=False, help_text="Accepted as a payment currency in the bazaar")
    backing_document = models.TextField(blank=True, help_text="What this asset is backed by (e.g. 1:1 PLN reserve held by …)")
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
# Tokenization
# ---------------------------------------------------------------------------

class TokenizationQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Tokenization records are permanent and cannot be reverted.")


class TokenizationStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DEFAULTED = "defaulted", "Defaulted"


class TokenizationDefaultReason(models.TextChoices):
    NO_LONGER_EXISTS = "no_longer_exists", "Underlying object no longer exists"
    BROKEN = "broken", "Underlying object is broken"
    LOST = "lost", "Underlying object is lost"
    OTHER = "other", "Other"


class Tokenization(models.Model):
    objects = TokenizationQuerySet.as_manager()

    real_world_object = models.ForeignKey(
        "inventory.RealWorldObject",
        on_delete=models.PROTECT,
        related_name="tokenizations",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="tokenizations",
    )
    supervisor = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supervised_tokenizations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TokenizationStatus.choices,
        default=TokenizationStatus.ACTIVE,
    )
    default_reason = models.CharField(
        max_length=40,
        choices=TokenizationDefaultReason.choices,
        blank=True,
    )
    default_note = models.TextField(blank=True)
    defaulted_at = models.DateTimeField(null=True, blank=True)
    defaulted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="defaulted_tokenizations",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["real_world_object"],
                name="unique_tokenization_object",
            ),
            models.UniqueConstraint(
                fields=["asset"],
                name="unique_tokenization_asset",
            ),
        ]
        indexes = [
            models.Index(fields=["real_world_object"]),
            models.Index(fields=["asset"]),
            models.Index(fields=["status"]),
            models.Index(fields=["supervisor"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.real_world_object} tokenized as {self.asset.unit_name}"

    def delete(self, *args, **kwargs):
        raise ValidationError("Tokenization records are permanent and cannot be reverted.")

    @property
    def is_defaulted(self) -> bool:
        return self.status == TokenizationStatus.DEFAULTED


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
        related_name='currency_peg',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'currencies'
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.name}"


# ---------------------------------------------------------------------------
# Obligation
# ---------------------------------------------------------------------------

class ObligationQuerySet(models.QuerySet):
    def payables_for(self, account):
        return self.filter(debtor_account=account)

    def receivables_for(self, account):
        return self.filter(creditor_account=account)

    def pending(self):
        return self.filter(status="pending")

    def overdue(self):
        from django.utils import timezone
        return self.filter(status="pending", due_at__lt=timezone.now())


class ObligationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    OVERDUE = "overdue", "Overdue"
    FULFILLED = "fulfilled", "Fulfilled"
    DEFAULTED = "defaulted", "Defaulted"


class Obligation(models.Model):
    objects = ObligationQuerySet.as_manager()

    reference = models.CharField(max_length=255, unique=True)
    order_reference = models.CharField(max_length=255, blank=True)
    debtor_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name='debtor_obligations'
    )
    creditor_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name='creditor_obligations'
    )
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='obligations')
    amount_base_units = models.BigIntegerField()
    due_at = models.DateTimeField()
    collateral_account = models.ForeignKey(
        LedgerAccount, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='collateral_obligations'
    )
    collateral_asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='collateral_obligations'
    )
    collateral_amount_base_units = models.BigIntegerField(default=0)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=ObligationStatus.choices, default=ObligationStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_at']

    def __str__(self):
        return f"{self.reference} — {self.debtor_account.code} owes {self.amount_display} {self.asset.unit_name}"

    @property
    def amount_display(self) -> Decimal:
        return from_base_units(self.amount_base_units, self.asset.decimals)

    @property
    def collateral_display(self) -> Decimal:
        if self.collateral_asset:
            return from_base_units(self.collateral_amount_base_units, self.collateral_asset.decimals)
        return Decimal(0)

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone
        return self.status == ObligationStatus.PENDING and timezone.now() > self.due_at

    def is_payable_for(self, account) -> bool:
        return self.debtor_account_id == account.pk

    def is_receivable_for(self, account) -> bool:
        return self.creditor_account_id == account.pk


# ---------------------------------------------------------------------------
# ContractTemplate
# ---------------------------------------------------------------------------

class Contract(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, editable=False, unique=True, db_index=True)
    name = models.CharField(max_length=255, unique=True)
    code = models.TextField(blank=True, help_text="Lapis smart-contract YAML.")
    global_state = models.JSONField(default=dict, blank=True, help_text="Runtime Lapis VM state (status, counters, etc.).")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.code:
            from .lapis.loader import loads_contract
            from .lapis.compiler import ContractFlowValidator
            from .lapis.exceptions import LapisValidationError
            try:
                tree = loads_contract(self.code, fmt="yaml")
                ContractFlowValidator().validate(tree)
            except LapisValidationError as exc:
                raise ValidationError({"code": str(exc)}) from exc


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------

class Agreement(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    source_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="agreements_as_source",
    )
    target_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="agreements_as_target",
    )
    contract = models.ForeignKey(
        Contract,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="agreements",
        help_text="Lapis contract governing this agreement.",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Agreement {self.uuid} ({self.source_account} → {self.target_account})"

    def clean(self):
        if self.source_account_id and self.target_account_id:
            if self.source_account_id == self.target_account_id:
                raise ValidationError("Source and target accounts must differ.")


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


# ---------------------------------------------------------------------------
# Lifecycle primitives are defined in toto.claims
# ---------------------------------------------------------------------------

