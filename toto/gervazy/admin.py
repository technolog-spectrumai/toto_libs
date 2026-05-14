from django.contrib import admin
from toto.core.base_admin import TotoModelAdmin

from .models import (
    CryptoAuditLog,
    EncryptedFile,
    EncryptedFileChunk,
    EncryptedPrivateKey,
    EncryptedSecret,
    UserVault,
    VaultMasterKey,
    WrappedDataKey,
)


class VaultMasterKeyInline(admin.TabularInline):
    model = VaultMasterKey
    extra = 0
    readonly_fields = ("algorithm", "version", "state", "created_at", "retired_at")
    fields = ("version", "state", "algorithm", "created_at", "retired_at")
    can_delete = False


class WrappedDataKeyInline(admin.TabularInline):
    model = WrappedDataKey
    extra = 0
    readonly_fields = ("vmk_version", "algorithm", "version", "state", "created_at", "rotated_at")
    fields = ("version", "vmk_version", "state", "algorithm", "created_at", "rotated_at")
    can_delete = False


@admin.register(UserVault)
class UserVaultAdmin(TotoModelAdmin):
    list_display = ("name", "owner", "kdf", "argon2_memory_cost", "argon2_iterations", "created_at")
    list_filter = ("kdf", "created_at")
    search_fields = ("name", "owner__username")
    readonly_fields = ("salt_info", "created_at")
    inlines = [VaultMasterKeyInline, WrappedDataKeyInline]
    fieldsets = (
        (None, {"fields": ("owner", "name", "notes")}),
        ("KDF parameters", {
            "fields": ("kdf", "kdf_version", "salt_info", "argon2_memory_cost", "argon2_iterations", "argon2_lanes"),
            "description": "Argon2id key derivation parameters. Salt is generated automatically.",
        }),
        ("Timestamps", {"fields": ("created_at",)}),
    )

    @admin.display(description="Salt")
    def salt_info(self, obj):
        return f"{len(obj.salt)} bytes" if obj.salt else "—"


@admin.register(VaultMasterKey)
class VaultMasterKeyAdmin(TotoModelAdmin):
    list_display = ("vault", "version", "state", "algorithm", "created_at", "retired_at")
    list_filter = ("state", "algorithm", "created_at")
    search_fields = ("vault__name",)
    readonly_fields = ("created_at",)


@admin.register(WrappedDataKey)
class WrappedDataKeyAdmin(TotoModelAdmin):
    list_display = ("vault", "version", "vmk_version", "state", "algorithm", "created_at")
    list_filter = ("state", "algorithm", "created_at")
    search_fields = ("vault__name",)
    readonly_fields = ("created_at",)


@admin.register(EncryptedSecret)
class EncryptedSecretAdmin(TotoModelAdmin):
    list_display = ("name", "vault", "purpose", "state", "version", "created_at", "expires_at", "is_expired")
    list_filter = ("state", "purpose", "created_at", "expires_at")
    search_fields = ("name", "purpose", "vault__name")
    readonly_fields = ("created_at", "ciphertext_info")
    fieldsets = (
        (None, {"fields": ("vault", "wrapped_key", "name", "purpose", "state")}),
        ("Ciphertext", {
            "fields": ("algorithm", "version", "ciphertext_info"),
            "description": "Raw ciphertext stored as binary. Never displayed in plaintext.",
        }),
        ("Lifecycle", {"fields": ("expires_at", "created_at")}),
    )

    @admin.display(boolean=True)
    def is_expired(self, obj):
        return obj.is_expired()

    @admin.display(description="Ciphertext")
    def ciphertext_info(self, obj):
        if not obj.ciphertext:
            return "—"
        size = len(obj.ciphertext)
        nonce_size = len(obj.nonce) if obj.nonce else 0
        aad_size = len(obj.aad) if obj.aad else 0
        return f"Encrypted ({size} bytes), nonce {nonce_size} bytes, aad {aad_size} bytes"


class EncryptedFileChunkInline(admin.TabularInline):
    model = EncryptedFileChunk
    extra = 0
    readonly_fields = ("index", "ciphertext_size", "ciphertext_sha256")
    fields = ("index", "ciphertext_size", "ciphertext_sha256")
    can_delete = False


@admin.register(EncryptedFile)
class EncryptedFileAdmin(TotoModelAdmin):
    list_display = ("pk", "vault", "owner", "mime_type", "state", "chunk_count", "plaintext_size", "uploaded_at")
    list_filter = ("state", "algorithm", "uploaded_at")
    search_fields = ("vault__name", "owner__username")
    readonly_fields = ("uploaded_at",)
    inlines = [EncryptedFileChunkInline]


@admin.register(EncryptedPrivateKey)
class EncryptedPrivateKeyAdmin(TotoModelAdmin):
    list_display = ("key_id", "vault", "key_type", "state", "created_at", "retired_at")
    list_filter = ("key_type", "state", "created_at")
    search_fields = ("key_id", "vault__name", "issuer")
    readonly_fields = ("created_at", "public_key_pem", "ciphertext_info")
    fieldsets = (
        (None, {"fields": ("vault", "wrapped_key", "key_id", "key_type", "issuer", "state")}),
        ("Public key", {"fields": ("public_key_pem",)}),
        ("Encrypted private key", {
            "fields": ("algorithm", "ciphertext_info"),
            "description": "Private key stored as AES-256-GCM ciphertext. Never exported in plaintext.",
        }),
        ("Lifecycle", {"fields": ("created_at", "retired_at")}),
    )

    @admin.display(description="Ciphertext")
    def ciphertext_info(self, obj):
        if not obj.encrypted_private_key:
            return "—"
        size = len(obj.encrypted_private_key)
        nonce_size = len(obj.nonce) if obj.nonce else 0
        aad_size = len(obj.aad) if obj.aad else 0
        return f"Encrypted ({size} bytes), nonce {nonce_size} bytes, aad {aad_size} bytes"


@admin.register(CryptoAuditLog)
class CryptoAuditLogAdmin(TotoModelAdmin):
    list_display = ("created_at", "actor", "vault", "action", "object_type", "object_id", "success")
    list_filter = ("success", "action", "created_at")
    search_fields = ("actor__username", "action", "object_type", "object_id", "reason")
    readonly_fields = (
        "id", "actor", "vault", "action", "object_type", "object_id",
        "success", "reason", "key_version", "ip_address", "user_agent", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
