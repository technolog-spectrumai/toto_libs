from __future__ import annotations

import uuid as _uuid

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# ---------------------------------------------------------------------------
# Constants — supported types
# ---------------------------------------------------------------------------

SUPPORTED_NODE_TYPES = [
    "contract",
    "manual",
    "note",
    "entitlement",
    "obligation",
    "schedule",
    "condition",
    "allocation",
    "event",
    "ledger_account",
    "asset",
    "ledger_transaction",
    "resource_type",
    "agreement",
]

SUPPORTED_EDGE_TYPES = [
    "related",
    "explains",
    "annotates",
    "grants",
    "creates_duty",
    "triggers",
    "gates",
    "requires",
    "allocates",
    "secures",
    "records",
    "settles",
    "settled_by",
    "held_by",
    "issued_by",
    "debtor",
    "creditor",
    "uses_asset",
    "has_resource_type",
    "references",
    "composes",
]

NODE_TYPE_SHAPE = {
    "contract":          "rectangle",
    "manual":            "round-rectangle",
    "note":              "bottom-round-rectangle",
    "entitlement":       "ellipse",
    "obligation":        "diamond",
    "schedule":          "barrel",
    "condition":         "round-diamond",
    "allocation":        "hexagon",
    "event":             "tag",
    "ledger_account":    "round-tag",
    "asset":             "pentagon",
    "ledger_transaction": "cut-rectangle",
    "resource_type":     "concave-hexagon",
    "agreement":         "round-pentagon",
}


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class Contract(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PENDING = "pending"
    STATUS_EXECUTED = "executed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING, "Pending Signatures"),
        (STATUS_EXECUTED, "Executed"),
    ]

    uuid = models.UUIDField(default=_uuid.uuid4, editable=False, unique=True, db_index=True)
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(
        blank=True,
        help_text="Human-readable description of what this contract represents.",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    body = models.TextField(
        blank=True,
        help_text="Human-readable text of this contract, authored freely.",
    )
    vault_pdf = models.ForeignKey(
        "gervazy.EncryptedFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contract_pdfs",
        help_text="Encrypted PDF stored in a gervazy vault.",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "contracts"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def check_and_execute(self):
        """Execute the contract if all required signatories have signed."""
        required = self.signatories.filter(is_required=True)
        if required.exists() and not required.filter(signed_at__isnull=True).exists():
            self.status = self.STATUS_EXECUTED
            self.executed_at = timezone.now()
            self.save(update_fields=["status", "executed_at"])


# ---------------------------------------------------------------------------
# ContractNode
# ---------------------------------------------------------------------------

class ContractNode(models.Model):
    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.CASCADE,
        related_name="nodes",
    )
    key = models.SlugField(
        max_length=120,
        help_text="Stable node id inside this contract, e.g. monthly_fee.",
    )
    node_type = models.CharField(max_length=40)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_manual = models.BooleanField(
        default=False,
        help_text="True when this node is manually authored and not backed by a claims/assets/instruments object.",
    )
    object_app = models.CharField(max_length=80, blank=True)
    object_model = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    position_x = models.FloatField(null=True, blank=True)
    position_y = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "contracts"
        unique_together = [("contract", "key")]
        ordering = ["contract", "key"]
        indexes = [
            models.Index(fields=["contract", "node_type"]),
            models.Index(fields=["contract", "is_manual"]),
            models.Index(fields=["object_app", "object_model", "object_id"]),
        ]

    def __str__(self):
        return f"{self.contract} / {self.key}"

    def clean(self):
        if self.node_type and self.node_type not in SUPPORTED_NODE_TYPES:
            raise ValidationError({"node_type": f"Unsupported node type: {self.node_type!r}."})

    def get_object(self):
        if not self.is_backed:
            return None
        try:
            Model = apps.get_model(self.object_app, self.object_model)
            return Model.objects.get(pk=self.object_id)
        except Exception:
            return None

    @property
    def is_backed(self):
        return bool(self.object_app and self.object_model and self.object_id)


# ---------------------------------------------------------------------------
# ContractEdge
# ---------------------------------------------------------------------------

class ContractEdge(models.Model):
    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.CASCADE,
        related_name="edges",
    )
    source = models.ForeignKey(
        "contracts.ContractNode",
        on_delete=models.CASCADE,
        related_name="outgoing_edges",
    )
    target = models.ForeignKey(
        "contracts.ContractNode",
        on_delete=models.CASCADE,
        related_name="incoming_edges",
    )
    edge_type = models.CharField(max_length=40)
    label = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "contracts"
        ordering = ["contract", "source__key", "target__key"]

    def __str__(self):
        return f"{self.source.key} --[{self.edge_type}]--> {self.target.key}"

    def clean(self):
        if self.source_id and self.target_id:
            if self.source.contract_id != self.contract_id:
                raise ValidationError("Source node belongs to a different contract.")
            if self.target.contract_id != self.contract_id:
                raise ValidationError("Target node belongs to a different contract.")
        if self.edge_type and self.edge_type not in SUPPORTED_EDGE_TYPES:
            raise ValidationError({"edge_type": f"Unsupported edge type: {self.edge_type!r}."})


# ---------------------------------------------------------------------------
# ContractSignatory
# ---------------------------------------------------------------------------

class ContractSignatory(models.Model):
    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.CASCADE,
        related_name="signatories",
    )
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="contract_signatories",
    )
    is_required = models.BooleanField(
        default=True,
        help_text="If True, this person must sign before the contract can be executed.",
    )
    signed_at = models.DateTimeField(null=True, blank=True)

    # Decorative pencil signature (base64 PNG canvas drawing).
    signature_data = models.TextField(
        blank=True,
        help_text="Base64-encoded PNG — decorative handwritten signature image.",
    )

    # Cryptographic signature fields (populated by gervazy.signing.SigningService).
    signing_payload = models.TextField(
        blank=True,
        help_text="Canonical UTF-8 payload that was signed.",
    )
    cryptographic_signature = models.TextField(
        blank=True,
        help_text="Base64-encoded Ed25519 signature over signing_payload.",
    )
    signing_key = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contract_signatures",
        help_text="The EncryptedPrivateKey used to produce the cryptographic signature.",
    )

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "contracts"
        unique_together = [("contract", "person")]
        ordering = ["added_at"]

    def __str__(self):
        signed = "signed" if self.signed_at else "pending"
        return f"{self.person} on {self.contract} [{signed}]"

    @property
    def has_signed(self):
        return self.signed_at is not None

    @property
    def is_cryptographically_signed(self):
        return bool(self.cryptographic_signature and self.signing_payload)
