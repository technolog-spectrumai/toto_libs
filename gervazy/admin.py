from .models import KeyRing
from django.contrib import admin
from .models import RSAKeyPair
from .batch import BatchAction  # assuming you have a BatchAction helper


@admin.register(KeyRing)
class KeyRingAdmin(admin.ModelAdmin):
    list_display = ('label', 'owner', 'created_at')
    search_fields = ('label', 'owner__username')
    readonly_fields = ('salt', 'created_at')
    list_filter = ('created_at',)


@admin.register(RSAKeyPair)
class RSAKeyPairAdmin(admin.ModelAdmin):
    list_display = ("key_id", "issuer", "created_at")
    search_fields = ("key_id", "issuer")
    readonly_fields = ("public_key_pem", "private_key_pem", "created_at")
    actions = ["generate_new_keypair"]

    @admin.action(description="Generate new RSA keypair for selected entries")
    def generate_new_keypair(self, request, queryset):
        def regenerate_one(obj):
            new_pair = RSAKeyPair.generate(obj.key_id, obj.issuer)
            obj.private_key_pem = new_pair.private_key_pem
            obj.public_key_pem = new_pair.public_key_pem
            obj.save()
            return obj

        result = BatchAction(queryset).run(regenerate_one)
        BatchAction.display_messages(result, self.message_user, request, verb="regenerate")


