from django.contrib import admin

from .models import LedgerPeer


@admin.register(LedgerPeer)
class LedgerPeerAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "platform_id", "status", "send_seq", "recv_seq",
                    "updated_at")
    readonly_fields = ("epoch", "peer_epoch", "send_seq", "recv_seq",
                       "our_public_key_pem", "peer_public_key_pem",
                       "secret_cheap_hash", "previous_cheap_hash",
                       "created_at", "updated_at")
    exclude = ("client_secret_encrypted", "previous_secret_encrypted",
               "our_private_key_encrypted")

    def has_delete_permission(self, request, obj=None):
        # A peer with history must be suspended, never deleted — the signed log
        # and checkpoints reference it.
        return False
