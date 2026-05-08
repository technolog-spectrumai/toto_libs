from django import forms
from .models import KeyRing
from .models import RSAKeyPair
from toto.core.batch import BatchAction
from .models import EnvironmentVariable, SecretKey, SecretPassword
from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from toto.core.base_admin import TotoModelAdmin


class EnvironmentVariableAdminForm(forms.ModelForm):
    value = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Set the value in the current server process. Leave blank to keep the current environment unchanged.",
    )

    class Meta:
        model = EnvironmentVariable
        fields = "__all__"

    def save(self, commit=True):
        instance = super().save(commit=commit)
        value = self.cleaned_data.get("value")

        if value:
            instance.set_value(value)
        elif not instance.active:
            instance.apply_to_environment()

        return instance


@admin.register(EnvironmentVariable)
class EnvironmentVariableAdmin(TotoModelAdmin):
    form = EnvironmentVariableAdminForm
    list_display = ("name", "active", "masked_value", "updated_at")
    list_filter = ("active", "created_at", "updated_at")
    search_fields = ("name", "notes")
    readonly_fields = ("created_at", "updated_at", "masked_value")
    fieldsets = (
        (None, {
            "fields": ("name", "active", "value", "masked_value"),
        }),
        ("Notes", {
            "fields": ("notes",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )


@admin.register(KeyRing)
class KeyRingAdmin(TotoModelAdmin):
    list_display = ('name', 'owner', 'created_at')
    search_fields = ('name', 'owner__username')
    readonly_fields = ('salt', 'created_at')
    list_filter = ('created_at',)


@admin.register(RSAKeyPair)
class RSAKeyPairAdmin(TotoModelAdmin):
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
class SecretKeyAdmin(TotoModelAdmin):
    list_display = (
        "id",
        "keyring",
        "size",
        "active",
        "created_at",
        "expires_at",
        "status_display",
    )
    list_filter = ("active", "size", "created_at", "expires_at", "keyring")
    search_fields = ("id",)
    readonly_fields = ("created_at", "masked_key")
    exclude = ("key_encrypted",)

    actions = ["reveal_selected_secrets", "rotate_selected_secrets"]

    # -------------------------
    # Display helpers
    # -------------------------
    def status_display(self, obj):
        if obj.is_expired():
            return format_html('<span style="color:red;">Expired ❌</span>')
        return format_html('<span style="color:green;">Active ✅</span>')
    status_display.short_description = "Status"

    def masked_key(self, obj):
        """Show masked key based on size."""
        return "*" * obj.size
    masked_key.short_description = "Secret (masked)"

    # -------------------------
    # Custom URLs
    # -------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "reveal/",
                self.admin_site.admin_view(self.reveal_view),
                name="secretkey_reveal",
            ),
            path(
                "rotate/",
                self.admin_site.admin_view(self.rotate_view),
                name="secretkey_rotate",
            ),
        ]
        return custom_urls + urls

    # -------------------------
    # Admin actions
    # -------------------------
    def reveal_selected_secrets(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse("admin:secretkey_reveal") + f"?ids={','.join(selected)}"
        return redirect(url)
    reveal_selected_secrets.short_description = (
        "Reveal selected secrets (requires passphrase)"
    )

    def rotate_selected_secrets(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse("admin:secretkey_rotate") + f"?ids={','.join(selected)}"
        return redirect(url)
    rotate_selected_secrets.short_description = (
        "Rotate selected secrets (requires passphrase)"
    )

    # -------------------------
    # Reveal view
    # -------------------------
    def reveal_view(self, request):
        ids = request.GET.get("ids", "").split(",")
        queryset = SecretKey.objects.filter(pk__in=ids)

        if request.method == "POST":
            passphrase = request.POST.get("passphrase")

            for secret in queryset:
                try:
                    decrypted = secret.get_key(passphrase)
                    self.message_user(
                        request,
                        f"Secret {secret.id}: {decrypted}",
                        messages.SUCCESS,
                    )
                except Exception:
                    self.message_user(
                        request,
                        f"Invalid passphrase for secret {secret.id}",
                        messages.ERROR,
                    )

            return redirect("..")

        return render(
            request,
            "admin/secretkey_reveal.html",
            {"ids": ids, "count": queryset.count()},
        )

    # -------------------------
    # Rotate view
    # -------------------------
    def rotate_view(self, request):
        ids = request.GET.get("ids", "").split(",")
        queryset = SecretKey.objects.filter(pk__in=ids)

        if request.method == "POST":
            passphrase = request.POST.get("passphrase")

            for secret in queryset:
                try:
                    # Validate passphrase by attempting decryption
                    secret.get_key(passphrase)

                    # Rotate using the same passphrase
                    secret.rotate(passphrase)

                    self.message_user(
                        request,
                        f"Rotated secret {secret.id}",
                        messages.SUCCESS,
                    )
                except Exception:
                    self.message_user(
                        request,
                        f"Invalid passphrase for secret {secret.id}",
                        messages.ERROR,
                    )

            return redirect("..")

        return render(
            request,
            "admin/secretkey_rotate.html",
            {"ids": ids, "count": queryset.count()},
        )


@admin.register(SecretPassword)
class SecretPasswordAdmin(TotoModelAdmin):
    list_display = ("id", "name", "secret_key", "environment_variable", "active", "created_at", "expires_at", "status_display")
    list_filter = ("active", "created_at", "expires_at", "secret_key", "environment_variable")
    search_fields = ("id", "name", "environment_variable__name")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("secret_key", "environment_variable")
    actions = ["reveal_linked_secret_keys", "rotate_linked_secret_keys"]

    # -------------------------
    # Display helpers
    # -------------------------
    def status_display(self, obj):
        if obj.is_expired():
            return format_html('<span style="color:red;">Expired ❌</span>')
        return format_html('<span style="color:green;">Active ✅</span>')
    status_display.short_description = "Status"

    @admin.action(description="Reveal linked SecretKey using environment variable")
    def reveal_linked_secret_keys(self, request, queryset):
        for secret_box in queryset.select_related("secret_key", "environment_variable"):
            try:
                decrypted = secret_box.get_secret_key()
                self.message_user(
                    request,
                    f"SecretKey {secret_box.secret_key_id}: {decrypted}",
                    messages.SUCCESS,
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f"Could not unlock SecretPassword {secret_box.id}: {exc}",
                    messages.ERROR,
                )

    @admin.action(description="Rotate linked SecretKey using environment variable")
    def rotate_linked_secret_keys(self, request, queryset):
        for secret_box in queryset.select_related("secret_key", "environment_variable"):
            try:
                secret_box.rotate_secret_key()
                self.message_user(
                    request,
                    f"Rotated SecretKey {secret_box.secret_key_id}",
                    messages.SUCCESS,
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f"Could not rotate SecretPassword {secret_box.id}: {exc}",
                    messages.ERROR,
                )
