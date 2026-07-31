"""The consumer's federation surface: federate to a platform, and see who is linked.

Two things here are new rather than moved:

* **"Federate to a platform"** replaces the connection-bundle paste. That bundle
  carried a client secret through a downloaded file and a human's clipboard; this
  takes a pairing code — useless to anyone who is not this host — and lets the two
  servers exchange the secret directly.
* **``FederatedIdentity`` is registered at all.** It had no admin, while being the
  precondition for every federated login: claims are matched only on a recorded
  ``(provider, sub)``. An operator could not see who was linked, audit it, or
  revoke it.

**Why the page has two steps rather than one.** Naming the platform first is not
decoration. A pairing code carries its own provider's address, so a single paste
box redeems whatever the code names — an operator handed the wrong code federates
this host to a stranger and finds out afterwards. Declaring the destination first
turns that into a comparison this host can make, and the step exists on the way
*out* so it happens before the code is transmitted anywhere.
"""
from __future__ import annotations

from urllib.parse import quote, urlparse

from django.conf import settings
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


def _same_platform(stored_url, canonical_target):
    """Does an already-stored connection point at the platform being federated to?

    Both sides go through pairing.platform_url so the comparison survives a
    trailing slash or a missing scheme; a stored value that will not normalise
    (a legacy row) simply reports "not the same", which errs toward showing the
    new-connection guidance rather than the renewal guidance.
    """
    from .pairing import PairingError, platform_url

    try:
        return platform_url(stored_url) == canonical_target
    except PairingError:
        return False


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
                "federate/",
                self.admin_site.admin_view(self.federate_view),
                name="sso_client_oidcproviderconfig_federate",
            ),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["federate_url"] = reverse(
            "admin:sso_client_oidcproviderconfig_federate",
        )
        return super().changelist_view(request, extra_context=extra_context)

    def federate_view(self, request):
        """Two steps: name the platform, then hand over its code.

        Step one is where the operator says what they are trying to do; step two
        is where they do it. The gap between the two is what lets this host check
        that the code it was given comes from the platform that was asked for.
        """
        from toto.sso_core import qr

        from .pairing import PairingError, pair, platform_url

        # admin_view() checks is_active and is_staff but not per-model rights, so
        # without this a staff user with no rights on this model reaches a page
        # that federates the whole host.
        if not self.has_change_permission(request):
            self.message_user(
                request, "You do not have permission to federate with a platform.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:index"))

        callback_uri = _callback_uri(request)
        context = {
            **self.admin_site.each_context(request),
            "title": "Federate to a platform",
            "opts": self.model._meta,
            "step": 1,
            "error": None,
            "code": "",
            "label": "",
            "target": (request.GET.get("target") or "").strip(),
            "callback_uri": callback_uri,
            "own_host": urlparse(callback_uri).hostname or "",
            "current": OIDCProviderConfig.objects.filter(active=True).first(),
        }

        if request.method != "POST":
            return render(request, "admin/sso_client/federate.html", context)

        # -- step 1: which platform? ------------------------------------------
        raw_target = (request.POST.get("target") or "").strip()
        context["target"] = raw_target
        try:
            target = platform_url(raw_target)
        except PairingError as exc:
            context["error"] = exc.message
            return render(request, "admin/sso_client/federate.html", context)

        if target.startswith("http://") and not settings.DEBUG:
            # Say it here rather than let it fail at the far end: /sso/enroll/
            # refuses plaintext, so this address cannot work whatever they paste.
            context["error"] = (
                f"{target} is not secure, and a platform will not accept a pairing "
                "code over plain HTTP. Use https://."
            )
            return render(request, "admin/sso_client/federate.html", context)

        context["target"] = target
        context["step"] = 2
        context["invite_url"] = (
            f"{target}/admin/sso_master/ssorelyingparty/invite/"
            f"?host={quote(context['own_host'])}"
        )
        # Is this a renewal of the connection this host already has, or a new one?
        # It changes the instruction: a renewal must go through the provider's
        # "Mint a re-pairing code" on the EXISTING registration, because only that
        # keeps the relying-party UUID stable — and the consumer matches on that
        # UUID, so a brand-new invite would instead create a second connection and
        # strand every linked account behind the old one. The consumer cannot
        # deep-link the re-pair page (it does not know the provider-side pk), so it
        # links the registrations list and says what to click.
        current = context["current"]
        context["renewal"] = bool(current and _same_platform(current.portal_url, target))
        context["registrations_url"] = f"{target}/admin/sso_master/ssorelyingparty/"

        if request.POST.get("action") != "pair":
            return render(request, "admin/sso_client/federate.html", context)

        # -- step 2: the code --------------------------------------------------
        context["label"] = request.POST.get("label") or ""
        code = (request.POST.get("code") or "").strip()
        upload = request.FILES.get("image")

        # A photograph is just another way of typing the code. When a picture is
        # supplied it WINS over whatever is in the textarea, rather than being
        # ignored unless the textarea is empty: a failed attempt re-fills the box
        # with the code that just failed, so if the operator's fix is to upload a
        # fresh QR, the stale text must not silently beat it. The decoded value
        # replaces the box contents so it shows what was actually used.
        if upload:
            try:
                code = qr.read(upload.read())
            except qr.QRError as exc:
                context["error"] = str(exc)
        context["code"] = code

        if context["error"]:
            return render(request, "admin/sso_client/federate.html", context)
        if not code:
            context["error"] = "Paste the pairing code, or upload a picture of it."
            return render(request, "admin/sso_client/federate.html", context)

        try:
            config = pair(
                code,
                callback_uri=callback_uri,
                label=request.POST.get("label") or "",
                expect_url=target,
            )
        except PairingError as exc:
            context["error"] = exc.message
            return render(request, "admin/sso_client/federate.html", context)

        self.message_user(
            request,
            f"Federated with {config.label}. People can now sign in here with "
            f"their {urlparse(config.portal_url).hostname} account.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse(
            "admin:sso_client_oidcproviderconfig_change", args=[config.pk],
        ))

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
