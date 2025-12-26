from .models import KeyRing
from .models import RSAKeyPair
from toto.batch import BatchAction
from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.utils.html import format_html
from .models import SecretKey


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


@admin.register(SecretKey)
class SecretKeyAdmin(admin.ModelAdmin):
    list_display = ("id", "size", "active", "created_at", "expires_at", "status_display")
    list_filter = ("active", "size", "created_at", "expires_at")
    search_fields = ("id",)
    readonly_fields = ("created_at", "masked_key")
    exclude = ("key",)

    actions = ["reveal_selected_secrets", "rotate_selected_secrets"]

    def status_display(self, obj):
        if obj.is_expired():
            return format_html('<span style="color:red;">Expired ❌</span>')
        return format_html('<span style="color:green;">Active ✅</span>')
    status_display.short_description = "Status"

    def masked_key(self, obj):
        """
        Show a masked version of the key: stars equal to the size field.
        """
        return "*" * obj.size
    masked_key.short_description = "Secret (masked)"

    # --- Extra URLs ---
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("reveal/", self.admin_site.admin_view(self.reveal_view), name="secretkey_reveal"),
            path("rotate/", self.admin_site.admin_view(self.rotate_view), name="secretkey_rotate"),
        ]
        return custom_urls + urls

    # --- Actions ---
    def reveal_selected_secrets(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse("admin:secretkey_reveal") + f"?ids={','.join(selected)}"
        return redirect(url)
    reveal_selected_secrets.short_description = "Reveal selected secrets (requires passphrase)"

    def rotate_selected_secrets(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse("admin:secretkey_rotate") + f"?ids={','.join(selected)}"
        return redirect(url)
    rotate_selected_secrets.short_description = "Rotate selected secrets (requires passphrase)"

    # --- Views ---
    def reveal_view(self, request):
        ids = request.GET.get("ids", "").split(",")
        queryset = SecretKey.objects.filter(pk__in=ids)

        if request.method == "POST":
            passphrase = request.POST.get("passphrase")
            for secret in queryset:
                if secret.passphrase == passphrase:

                    self.message_user(request, f"Secret {secret.id}: {secret.key}", messages.SUCCESS)
                else:
                    self.message_user(request, f"Invalid passphrase for {secret.id}", messages.ERROR)
            return redirect("..")

        return render(request, "admin/secretkey_reveal.html", {"ids": ids})

    def rotate_view(self, request):
        ids = request.GET.get("ids", "").split(",")
        queryset = SecretKey.objects.filter(pk__in=ids)

        if request.method == "POST":
            passphrase = request.POST.get("passphrase")
            for secret in queryset:
                if secret.passphrase == passphrase:
                    secret.rotate(passphrase)
                    self.message_user(request, f"Rotated secret {secret.id}", messages.SUCCESS)
                else:
                    self.message_user(request, f"Invalid passphrase for {secret.id}", messages.ERROR)
            return redirect("..")

        return render(request, "admin/secretkey_rotate.html", {"ids": ids})


