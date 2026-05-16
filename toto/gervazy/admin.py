from django.contrib import admin

from toto.core.base_admin import TotoModelAdmin

from .models import (
    CryptoAuditLog,
    EncryptedFile,
    EncryptedFileChunk,
    EncryptedPrivateKey,
    EncryptedSecret,
    UserStrongbox,
    VaultMasterKey,
    WrappedDataKey,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def bytes_info(value):
    if not value:
        return "—"
    return f"{len(value)} bytes"


def short_uuid(value):
    if not value:
        return "—"
    return str(value)[:8]


# ---------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------

class VaultMasterKeyInline(admin.TabularInline):
    model = VaultMasterKey
    extra = 0
    can_delete = False

    readonly_fields = (
        "id",
        "algorithm",
        "version",
        "state",
        "nonce_info",
        "encrypted_vmk_info",
        "created_at",
        "retired_at",
    )

    fields = (
        "version",
        "state",
        "algorithm",
        "nonce_info",
        "encrypted_vmk_info",
        "created_at",
        "retired_at",
    )

    @admin.display(description="Nonce")
    def nonce_info(self, obj):
        return bytes_info(obj.nonce)

    @admin.display(description="Encrypted VMK")
    def encrypted_vmk_info(self, obj):
        return bytes_info(obj.encrypted_vmk)

    def has_add_permission(self, request, obj=None):
        return False


class WrappedDataKeyInline(admin.TabularInline):
    model = WrappedDataKey
    extra = 0
    can_delete = False

    readonly_fields = (
        "id",
        "vmk",
        "vmk_version_display",
        "algorithm",
        "version",
        "state",
        "nonce_info",
        "encrypted_dek_info",
        "created_at",
        "rotated_at",
    )

    fields = (
        "version",
        "vmk",
        "vmk_version_display",
        "state",
        "algorithm",
        "nonce_info",
        "encrypted_dek_info",
        "created_at",
        "rotated_at",
    )

    @admin.display(description="VMK version")
    def vmk_version_display(self, obj):
        return obj.vmk.version if obj and obj.vmk_id else "—"

    @admin.display(description="Nonce")
    def nonce_info(self, obj):
        return bytes_info(obj.nonce)

    @admin.display(description="Encrypted DEK")
    def encrypted_dek_info(self, obj):
        return bytes_info(obj.encrypted_dek)

    def has_add_permission(self, request, obj=None):
        return False


class EncryptedFileChunkInline(admin.TabularInline):
    model = EncryptedFileChunk
    extra = 0
    can_delete = False

    readonly_fields = (
        "id",
        "index",
        "nonce_info",
        "ciphertext_size",
        "ciphertext_sha256",
    )

    fields = (
        "index",
        "nonce_info",
        "ciphertext_size",
        "ciphertext_sha256",
    )

    @admin.display(description="Nonce")
    def nonce_info(self, obj):
        return bytes_info(obj.nonce)

    def has_add_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------
# UserStrongbox
# ---------------------------------------------------------------------

@admin.register(UserStrongbox)
class UserStrongboxAdmin(TotoModelAdmin):
    list_display = (
        "name",
        "owner",
        "kdf",
        "argon2_memory_cost",
        "argon2_iterations",
        "argon2_lanes",
        "created_at",
        "updated_at",
    )

    list_filter = (
        "kdf",
        "created_at",
        "updated_at",
    )

    search_fields = (
        "name",
        "owner__username",
        "owner__email",
    )

    readonly_fields = (
        "id",
        "salt_info",
        "created_at",
        "updated_at",
    )

    inlines = [VaultMasterKeyInline, WrappedDataKeyInline]

    fieldsets = (
        (None, {"fields": ("id", "owner", "name", "notes")}),
        (
            "KDF parameters",
            {
                "fields": (
                    "kdf",
                    "kdf_version",
                    "salt_info",
                    "argon2_memory_cost",
                    "argon2_iterations",
                    "argon2_lanes",
                ),
                "description": (
                    "Argon2id key derivation parameters. "
                    "The salt is generated automatically and is never displayed raw."
                ),
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Salt")
    def salt_info(self, obj):
        return bytes_info(obj.salt)


# ---------------------------------------------------------------------
# VaultMasterKey
# ---------------------------------------------------------------------

@admin.register(VaultMasterKey)
class VaultMasterKeyAdmin(TotoModelAdmin):
    list_display = (
        "strongbox",
        "version",
        "state",
        "algorithm",
        "nonce_info",
        "encrypted_vmk_info",
        "created_at",
        "retired_at",
    )

    list_filter = ("state", "algorithm", "created_at", "retired_at")
    search_fields = ("strongbox__name", "strongbox__owner__username")
    readonly_fields = ("id", "nonce_info", "encrypted_vmk_info", "created_at")

    fieldsets = (
        (None, {"fields": ("id", "strongbox", "version", "state", "algorithm")}),
        (
            "Encrypted key material",
            {
                "fields": ("nonce_info", "encrypted_vmk_info"),
                "description": "Raw encrypted VMK bytes are not displayed.",
            },
        ),
        ("Lifecycle", {"fields": ("created_at", "retired_at")}),
    )

    @admin.display(description="Nonce")
    def nonce_info(self, obj):
        return bytes_info(obj.nonce)

    @admin.display(description="Encrypted VMK")
    def encrypted_vmk_info(self, obj):
        return bytes_info(obj.encrypted_vmk)


# ---------------------------------------------------------------------
# WrappedDataKey
# ---------------------------------------------------------------------

@admin.register(WrappedDataKey)
class WrappedDataKeyAdmin(TotoModelAdmin):
    list_display = (
        "strongbox",
        "version",
        "vmk_version_display",
        "state",
        "algorithm",
        "nonce_info",
        "encrypted_dek_info",
        "created_at",
        "rotated_at",
    )

    list_filter = ("state", "algorithm", "created_at", "rotated_at")
    search_fields = ("strongbox__name", "strongbox__owner__username", "vmk__strongbox__name")
    readonly_fields = ("id", "vmk_version_display", "nonce_info", "encrypted_dek_info", "created_at")

    fieldsets = (
        (
            None,
            {"fields": ("id", "strongbox", "vmk", "vmk_version_display", "version", "state", "algorithm")},
        ),
        (
            "Encrypted key material",
            {
                "fields": ("nonce_info", "encrypted_dek_info"),
                "description": "Raw encrypted DEK bytes are not displayed.",
            },
        ),
        ("Lifecycle", {"fields": ("created_at", "rotated_at")}),
    )

    @admin.display(description="VMK version")
    def vmk_version_display(self, obj):
        return obj.vmk.version if obj and obj.vmk_id else "—"

    @admin.display(description="Nonce")
    def nonce_info(self, obj):
        return bytes_info(obj.nonce)

    @admin.display(description="Encrypted DEK")
    def encrypted_dek_info(self, obj):
        return bytes_info(obj.encrypted_dek)


# ---------------------------------------------------------------------
# EncryptedSecret
# ---------------------------------------------------------------------

@admin.register(EncryptedSecret)
class EncryptedSecretAdmin(TotoModelAdmin):
    list_display = (
        "name",
        "strongbox",
        "purpose",
        "state",
        "version",
        "ciphertext_info",
        "created_at",
        "updated_at",
        "expires_at",
        "is_expired_display",
    )

    list_filter = ("state", "purpose", "algorithm", "created_at", "updated_at", "expires_at")
    search_fields = ("name", "purpose", "strongbox__name", "strongbox__owner__username")

    readonly_fields = (
        "id",
        "ciphertext_info",
        "nonce_info",
        "aad_info",
        "created_at",
        "updated_at",
        "rotated_at",
        "is_expired_display",
        "can_decrypt_display",
    )

    fieldsets = (
        (None, {"fields": ("id", "strongbox", "wrapped_key", "name", "purpose", "state")}),
        (
            "Ciphertext",
            {
                "fields": ("algorithm", "version", "ciphertext_info", "nonce_info", "aad_info"),
                "description": "Secret value is encrypted. Plaintext is never displayed or editable in Django admin.",
            },
        ),
        (
            "Lifecycle",
            {
                "fields": (
                    "expires_at",
                    "is_expired_display",
                    "can_decrypt_display",
                    "created_at",
                    "updated_at",
                    "rotated_at",
                )
            },
        ),
    )

    @admin.display(description="Ciphertext")
    def ciphertext_info(self, obj):
        return bytes_info(obj.ciphertext)

    @admin.display(description="Nonce")
    def nonce_info(self, obj):
        return bytes_info(obj.nonce)

    @admin.display(description="AAD")
    def aad_info(self, obj):
        return bytes_info(obj.aad)

    @admin.display(description="Expired?", boolean=True)
    def is_expired_display(self, obj):
        return obj.is_expired()

    @admin.display(description="Can decrypt?", boolean=True)
    def can_decrypt_display(self, obj):
        return obj.can_decrypt() if hasattr(obj, "can_decrypt") else False


# ---------------------------------------------------------------------
# EncryptedFile
# ---------------------------------------------------------------------

@admin.register(EncryptedFile)
class EncryptedFileAdmin(TotoModelAdmin):
    list_display = (
        "id_short",
        "strongbox",
        "owner",
        "mime_type",
        "state",
        "chunk_count",
        "plaintext_size",
        "ciphertext_size",
        "uploaded_at",
    )

    list_filter = ("state", "algorithm", "mime_type", "uploaded_at")
    search_fields = ("strongbox__name", "owner__username", "owner__email", "mime_type", "ciphertext_sha256")
    readonly_fields = ("id", "original_name_info", "original_name_nonce_info", "ciphertext_sha256", "uploaded_at")
    inlines = [EncryptedFileChunkInline]

    fieldsets = (
        (None, {"fields": ("id", "strongbox", "owner", "wrapped_key", "state", "mime_type", "file")}),
        (
            "Encrypted original filename",
            {
                "fields": ("original_name_info", "original_name_nonce_info"),
                "description": "Original filename is stored encrypted.",
            },
        ),
        ("Chunking", {"fields": ("nonce_strategy", "chunk_size", "chunk_count")}),
        ("Sizes and integrity", {"fields": ("plaintext_size", "ciphertext_size", "ciphertext_sha256")}),
        ("Crypto", {"fields": ("algorithm", "version")}),
        ("Timestamps", {"fields": ("uploaded_at",)}),
    )

    @admin.display(description="ID")
    def id_short(self, obj):
        return short_uuid(obj.id)

    @admin.display(description="Encrypted original name")
    def original_name_info(self, obj):
        return bytes_info(obj.original_name_encrypted)

    @admin.display(description="Original name nonce")
    def original_name_nonce_info(self, obj):
        return bytes_info(obj.original_name_nonce)


# ---------------------------------------------------------------------
# EncryptedPrivateKey
# ---------------------------------------------------------------------

@admin.register(EncryptedPrivateKey)
class EncryptedPrivateKeyAdmin(TotoModelAdmin):
    list_display = (
        "key_id",
        "strongbox",
        "key_type",
        "issuer",
        "state",
        "ciphertext_info",
        "created_at",
        "retired_at",
    )

    list_filter = ("key_type", "state", "algorithm", "created_at", "retired_at")
    search_fields = ("key_id", "strongbox__name", "strongbox__owner__username", "issuer")

    readonly_fields = (
        "id",
        "public_key_pem",
        "ciphertext_info",
        "nonce_info",
        "aad_info",
        "created_at",
        "retired_at",
    )

    fieldsets = (
        (None, {"fields": ("id", "strongbox", "wrapped_key", "key_id", "key_type", "issuer", "state")}),
        ("Public key", {"fields": ("public_key_pem",), "description": "Public key is safe to display."}),
        (
            "Encrypted private key",
            {
                "fields": ("algorithm", "ciphertext_info", "nonce_info", "aad_info"),
                "description": "Private key is AES-256-GCM encrypted. Plaintext private key is never displayed.",
            },
        ),
        ("Lifecycle", {"fields": ("created_at", "retired_at")}),
    )

    @admin.display(description="Ciphertext")
    def ciphertext_info(self, obj):
        return bytes_info(obj.encrypted_private_key)

    @admin.display(description="Nonce")
    def nonce_info(self, obj):
        return bytes_info(obj.nonce)

    @admin.display(description="AAD")
    def aad_info(self, obj):
        return bytes_info(obj.aad)


# ---------------------------------------------------------------------
# CryptoAuditLog
# ---------------------------------------------------------------------

@admin.register(CryptoAuditLog)
class CryptoAuditLogAdmin(TotoModelAdmin):
    list_display = (
        "created_at",
        "actor",
        "strongbox",
        "action",
        "object_type",
        "object_id",
        "success",
        "ip_address",
    )

    list_filter = ("success", "action", "object_type", "created_at")
    search_fields = (
        "actor__username",
        "actor__email",
        "strongbox__name",
        "action",
        "object_type",
        "object_id",
        "reason",
        "ip_address",
    )

    readonly_fields = (
        "id",
        "actor",
        "strongbox",
        "action",
        "object_type",
        "object_id",
        "success",
        "reason",
        "key_version",
        "ip_address",
        "user_agent",
        "created_at",
    )

    fieldsets = (
        (None, {"fields": ("id", "created_at", "actor", "strongbox", "action", "success")}),
        ("Object", {"fields": ("object_type", "object_id", "key_version")}),
        ("Request metadata", {"fields": ("ip_address", "user_agent")}),
        ("Reason", {"fields": ("reason",)}),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
