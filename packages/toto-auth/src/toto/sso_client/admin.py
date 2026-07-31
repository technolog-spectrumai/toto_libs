"""The consumer's federation surface: join a platform, and see who is linked.

Two things here are new rather than moved:

* **"Join a platform"** replaces the connection-bundle paste. That bundle carried
  a client secret through a downloaded file and a human's clipboard; this takes a
  pairing code — useless to anyone who is not this host — and lets the two servers
  exchange the secret directly.
* **``FederatedIdentity`` is registered at all.** It had no admin, while being the
  precondition for every federated login: claims are matched only on a recorded
  ``(provider, sub)``. An operator could not see who was linked, audit it, or
  revoke it.
"""
from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import NoReverseMatch, path, reverse

from .models import FederatedIdentity, OIDCProviderConfig


def _callback_uri(request):
    """The exact URL this host will send at /authorize.

    Derived from the live request rather than from configuration, because the
    provider registers it verbatim and then compares it as an exact string — a
    value typed by hand is the most common federation misconfiguration there is.
    """
    try:
        return request.build_absolute_uri(reverse("sso:callback"))
    except NoReverseMatch:
        return ""


@admin.register(OIDCProviderConfig)
class OIDCProviderConfigAdmin(admin.ModelAdmin):
    list_display = ["label", "client_id", "portal_url", "active", "secret_state", "paired_at"]
    list_filter = ["active"]
    search_fields = ["label", "client_id", "portal_url"]

    # Everything the provider decides is readonly. Typing `roles` here when the
    # provider did not grant it makes the provider reject EVERY login with a bare
    # 400 — the scope check is a subset test the consumer cannot win by asking.
    readonly_fields = [
        "client_id", "portal_url", "scopes", "issuer", "authorization_endpoint",
        "token_endpoint", "userinfo_endpoint", "jwks_uri", "callback_uri",
        "remote_client_uuid", "secret", "secret_state", "paired_at",
        "imported_at", "last_pair_error", "identity_count",
    ]
    fieldsets = (
        (None, {"fields": ("label", "active")}),
        ("Pairing", {"fields": ("secret_state", "paired_at", "last_pair_error",
                                "identity_count")}),
        ("From the provider", {
            "fields": ("portal_url", "client_id", "scopes", "callback_uri",
                       "remote_client_uuid", "secret"),
            "description": "Set by the provider when you paired. Re-pair to change any of it.",
        }),
        ("Endpoints", {
            "fields": ("issuer", "authorization_endpoint", "token_endpoint",
                       "userinfo_endpoint", "jwks_uri"),
            "classes": ("collapse",),
        }),
    )
    actions = ["retire"]

    def has_add_permission(self, request):
        # A connection is made by pairing, never by hand: a hand-made row has no
        # secret and no registration on the other side, so it can only fail.
        return False

    def has_delete_permission(self, request, obj=None):
        # FederatedIdentity.provider is PROTECT, so a delete fails anyway once
        # anyone has signed in. Retiring is the reversible act.
        return False

    def get_urls(self):
        return [
            path(
                "join/",
                self.admin_site.admin_view(self.join_view),
                name="sso_client_oidcproviderconfig_join",
            ),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["join_url"] = reverse("admin:sso_client_oidcproviderconfig_join")
        return super().changelist_view(request, extra_context=extra_context)

    def join_view(self, request):
        """Paste a pairing code, or upload a photograph of one."""
        from toto.sso_core import qr

        from .pairing import PairingError, pair

        # admin_view() checks is_active and is_staff but not per-model rights.
        if not self.has_change_permission(request):
            self.message_user(
                request, "You do not have permission to pair with a platform.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:index"))

        context = {
            **self.admin_site.each_context(request),
            "title": "Join a platform",
            "opts": self.model._meta,
            "error": None,
            "code": "",
            "callback_uri": _callback_uri(request),
        }

        if request.method == "POST":
            code = (request.POST.get("code") or "").strip()
            upload = request.FILES.get("image")

            # A photograph is just another way of typing the code; both converge
            # here so there is one pairing path to get right.
            if upload and not code:
                try:
                    code = qr.read(upload.read())
                except qr.QRError as exc:
                    context["error"] = str(exc)
            context["code"] = code

            if not context["error"]:
                if not code:
                    context["error"] = "Paste a pairing code, or upload a picture of one."
                else:
                    try:
                        config = pair(
                            code,
                            callback_uri=context["callback_uri"],
                            label=request.POST.get("label") or "",
                        )
                    except PairingError as exc:
                        context["error"] = exc.message
                    else:
                        self.message_user(
                            request,
                            f"Paired with {config.label}. Federated sign-in is live.",
                            level=messages.SUCCESS,
                        )
                        return HttpResponseRedirect(reverse(
                            "admin:sso_client_oidcproviderconfig_change", args=[config.pk],
                        ))

        return render(request, "admin/sso_client/join.html", context)

    @admin.display(description="Client secret")
    def secret_state(self, obj):
        if obj.secret_id is None:
            return "not stored — pair to set one"
        from toto.sso_core import vault

        try:
            vault.read_secret(obj.secret, create=False)
        except Exception as exc:                # noqa: BLE001
            return f"stored, but unreadable: {exc}"
        return "stored in the SSO vault, readable"

    @admin.display(description="Linked accounts")
    def identity_count(self, obj):
        return obj.identities.count()

    @admin.action(description="Retire the selected connections")
    def retire(self, request, queryset):
        count = queryset.update(active=False)
        self.message_user(
            request,
            f"Retired {count} connection(s). Linked accounts are untouched; "
            "pair again to restore federated sign-in.",
        )


@admin.register(FederatedIdentity)
class FederatedIdentityAdmin(admin.ModelAdmin):
    """Who on this platform answers to which account on the provider.

    Read-only. This table is the *precondition* for a federated sign-in — claims
    are matched only on a recorded ``(provider, sub)``, never on email or
    username, because matching on those let a provider account adopt a local one
    that happened to share an address. Creating a row by hand would be exactly
    that adoption, so it is not offered; unlinking is.
    """

    list_display = ["user", "provider", "sub", "provisioned", "linked_at", "last_login_at"]
    list_filter = ["provider", "provisioned"]
    search_fields = ["user__username", "user__email", "sub"]
    actions = ["unlink"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Unlink the selected identities")
    def unlink(self, request, queryset):
        """Break the link without touching the account.

        The local user survives with whatever password it had; it simply stops
        being reachable through the provider. An account that federation itself
        created has no usable password, so unlinking leaves it unreachable — said
        plainly here rather than discovered later.
        """
        provisioned = queryset.filter(provisioned=True).count()
        count = queryset.count()
        queryset.delete()
        note = ""
        if provisioned:
            note = (
                f" {provisioned} of them were created by federation and now have "
                "no way to sign in."
            )
        self.message_user(request, f"Unlinked {count} identity/identities.{note}")
