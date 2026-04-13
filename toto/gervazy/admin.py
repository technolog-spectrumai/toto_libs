from django import forms
from .models import KeyRing
from .models import RSAKeyPair
from toto.core.batch import BatchAction
from .models import SecretKey, SecretPassword
from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from toto.core.base_admin import TotoModelAdmin


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


class SecretPasswordAdminForm(forms.ModelForm):
    raw_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Enter the password to encrypt (leave blank to keep existing)."
    )

    passphrase = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Passphrase required to encrypt the password."
    )

    class Meta:
        model = SecretPassword
        fields = ["name", "keyring", "active", "expires_at"]

    def save(self, commit=True):
        instance = super().save(commit=False)

        raw_password = self.cleaned_data.get("raw_password")
        passphrase = self.cleaned_data.get("passphrase")

        # If user typed a new password → require passphrase
        if raw_password:
            if not passphrase:
                raise forms.ValidationError("Passphrase is required to encrypt the password.")
            instance.set_password(raw_password, passphrase)

        if commit:
            instance.save()

        return instance



@admin.register(SecretPassword)
class SecretPasswordAdmin(TotoModelAdmin):
    form = SecretPasswordAdminForm
    list_display = ("id", "name", "keyring", "active", "created_at", "expires_at", "status_display")
    list_filter = ("active", "created_at", "expires_at", "keyring")
    search_fields = ("id",)
    readonly_fields = ("created_at", "masked_password")
    exclude = ("password_encrypted",)
    actions = ["reveal_selected_passwords", "rotate_selected_passwords"]

    # -------------------------
    # Display helpers
    # -------------------------
    def status_display(self, obj):
        if obj.is_expired():
            return format_html('<span style="color:red;">Expired ❌</span>')
        return format_html('<span style="color:green;">Active ✅</span>')
    status_display.short_description = "Status"

    def masked_password(self, obj):
        """Mask password length with stars."""
        try:
            # Try to get length without decrypting
            return "********"
        except Exception:
            return "********"
    masked_password.short_description = "Password (masked)"

    # -------------------------
    # Custom URLs
    # -------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("reveal/", self.admin_site.admin_view(self.reveal_view), name="secretpassword_reveal"),
            path("rotate/", self.admin_site.admin_view(self.rotate_view), name="secretpassword_rotate"),
        ]
        return custom_urls + urls

    # -------------------------
    # Admin actions
    # -------------------------
    def reveal_selected_passwords(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse("admin:secretpassword_reveal") + f"?ids={','.join(selected)}"
        return redirect(url)
    reveal_selected_passwords.short_description = "Reveal selected passwords (requires passphrase)"

    def rotate_selected_passwords(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse("admin:secretpassword_rotate") + f"?ids={','.join(selected)}"
        return redirect(url)
    rotate_selected_passwords.short_description = "Re-encrypt selected passwords (requires passphrase)"

    # -------------------------
    # Reveal view
    # -------------------------
    def reveal_view(self, request):
        ids = request.GET.get("ids", "").split(",")
        queryset = SecretPassword.objects.filter(pk__in=ids)

        if request.method == "POST":
            passphrase = request.POST.get("passphrase")

            for secret in queryset:
                try:
                    decrypted = secret.get_password(passphrase)
                    self.message_user(
                        request,
                        f"Password {secret.id}: {decrypted}",
                        messages.SUCCESS,
                    )
                except Exception:
                    self.message_user(
                        request,
                        f"Invalid passphrase for password {secret.id}",
                        messages.ERROR,
                    )

            return redirect("..")

        return render(
            request,
            "admin/secretpassword_reveal.html",
            {"ids": ids, "count": queryset.count()},
        )

    # -------------------------
    # Rotate view
    # -------------------------
    def rotate_view(self, request):
        ids = request.GET.get("ids", "").split(",")
        queryset = SecretPassword.objects.filter(pk__in=ids)

        if request.method == "POST":
            passphrase = request.POST.get("passphrase")

            for secret in queryset:
                try:
                    # Validate passphrase
                    secret.get_password(passphrase)

                    # Re-encrypt with same passphrase
                    secret.rotate(passphrase)

                    self.message_user(
                        request,
                        f"Re-encrypted password {secret.id}",
                        messages.SUCCESS,
                    )
                except Exception:
                    self.message_user(
                        request,
                        f"Invalid passphrase for password {secret.id}",
                        messages.ERROR,
                    )

            return redirect("..")

        return render(
            request,
            "admin/secretpassword_rotate.html",
            {"ids": ids, "count": queryset.count()},
        )


