"""The provider's federation surface, and the hardening that had to come with it.

Two halves:

* **Pairing.** "Invite a platform" on the relying-party changelist, and "Mint a
  re-pairing code" on an existing one. Both render a QR plus the same string in a
  copy field. Nothing else in this app ever shows a credential.
* **Hardening.** The token and authorization-code admins were a working
  impersonation primitive: neither disabled ``add``, ``save()`` generates the
  value when it is blank, and ``readonly_fields`` then *displayed* it — so a
  staff user could add a code row for any (client, user) pair, read the generated
  code off the page, and exchange it at ``/token`` for that user's tokens.
  ``code_challenge`` is nullable and ``verify_pkce`` returns True when there is no
  challenge, so PKCE did not stand in the way. Both are now add-proof,
  change-proof, and show fingerprints rather than values.
"""
from __future__ import annotations

import hashlib

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from .models import (
    MAX_INVITE_TTL_MINUTES,
    MIN_INVITE_TTL_MINUTES,
    SSOAccessToken,
    SSOAuthorizationCode,
    SSOFederationInvite,
    SSORelyingParty,
    SSOSigningKey,
    SSOSubject,
)


def _fingerprint(value: str) -> str:
    """A stable handle for a credential, safe to render.

    Enough to correlate a row with a log line; useless to anyone who wants to use
    it. Replaces displaying the credential itself.
    """
    if not value:
        return "—"
    return f"{hashlib.sha256(value.encode()).hexdigest()[:8]}…"


@admin.register(SSORelyingParty)
class SSORelyingPartyAdmin(admin.ModelAdmin):
    list_display = [
        "name", "client_id", "origin", "active", "trusted", "secret_state", "last_seen",
    ]
    list_filter = ["client_type", "active", "trusted", "pairing_managed"]
    search_fields = ["name", "client_id"]
    # client_secret_hash is deliberately absent from the form. It is a PBKDF2
    # hash: any value typed by hand makes check_password fail forever, which is a
    # silent, total federation outage that looks like a client bug. It is written
    # only by rotate_client_secret.
    exclude = ["client_secret_hash"]
    readonly_fields = [
        "created_at", "updated_at", "secret_state", "pairing_state", "last_seen",
    ]

    fieldsets = (
        (None, {"fields": ("name", "client_id", "active", "trusted")}),
        ("Access", {"fields": ("redirect_uris", "allowed_scopes", "client_type")}),
        ("Pairing", {"fields": ("pairing_state", "secret_state", "last_seen")}),
        ("Bookkeeping", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_urls(self):
        return [
            path(
                "invite/",
                self.admin_site.admin_view(self.invite_view),
                name="sso_master_ssorelyingparty_invite",
            ),
            path(
                "<uuid:pk>/repair/",
                self.admin_site.admin_view(self.repair_view),
                name="sso_master_ssorelyingparty_repair",
            ),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["invite_url"] = reverse("admin:sso_master_ssorelyingparty_invite")
        return super().changelist_view(request, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        from django.urls import NoReverseMatch

        extra_context = extra_context or {}
        rp = SSORelyingParty.objects.filter(pk=object_id).first()
        if rp is not None and rp.pairing_managed:
            extra_context["repair_url"] = reverse(
                "admin:sso_master_ssorelyingparty_repair", args=[object_id],
            )
        try:
            # Pre-existing: an in-process authorize round trip that renders the
            # claims this party would receive. It never calls /token, so it proves
            # nothing about the client secret or the redirect URI — kept because it
            # is still the quickest way to see a claim set.
            extra_context["test_login_url"] = reverse(
                "sso:admin_test_login", args=[object_id],
            )
        except NoReverseMatch:
            pass
        return super().change_view(request, object_id, form_url, extra_context)

    # -- the two pairing pages ------------------------------------------------

    def invite_view(self, request):
        """Mint a code for a platform this provider has never seen."""
        return self._pairing_page(request, relying_party=None)

    def repair_view(self, request, pk):
        """Mint a code for an existing registration.

        The only way to re-pair, and it exists so an operator never has to know
        that "invite" and "re-pair" are different operations. It keeps the relying
        party's UUID, which every live access token points at.
        """
        rp = SSORelyingParty.objects.filter(pk=pk).first()
        if rp is None:
            self.message_user(request, "No such relying party.", level=messages.ERROR)
            return HttpResponseRedirect(
                reverse("admin:sso_master_ssorelyingparty_changelist"),
            )
        return self._pairing_page(request, relying_party=rp)

    def _pairing_page(self, request, *, relying_party):
        from datetime import timedelta
        from urllib.parse import urlparse

        from toto.sso_core import qr

        from .enrollment import EnrollmentError, mint
        from .services import get_public_base_url

        # admin_view() checks is_active and is_staff but NOT per-model
        # permissions, so without this a staff user with no rights on this model
        # would reach a page that mints credentials.
        if not self.has_change_permission(request):
            self.message_user(
                request, "You do not have permission to pair platforms.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:index"))

        form = {
            "expected_host": "",
            "roles": False,
            "trusted": True,
            "ttl_minutes": MIN_INVITE_TTL_MINUTES,
        }
        if relying_party is not None:
            # Re-pairing: whatever this registration already has is the default.
            for uri in relying_party.redirect_uri_list():
                host = urlparse(uri).hostname or ""
                if host:
                    form["expected_host"] = host
                    break
            form["roles"] = "roles" in relying_party.scope_list()
            form["trusted"] = relying_party.trusted

        context = {
            **self.admin_site.each_context(request),
            "title": "Re-pair a platform" if relying_party else "Invite a platform",
            "opts": self.model._meta,
            "relying_party": relying_party,
            "error": None,
            "minted": None,
            "form": form,
            "min_ttl": MIN_INVITE_TTL_MINUTES,
            "max_ttl": MAX_INVITE_TTL_MINUTES,
        }

        if request.method == "POST":
            form["expected_host"] = (request.POST.get("expected_host") or "").strip()
            form["roles"] = bool(request.POST.get("roles"))
            form["trusted"] = bool(request.POST.get("trusted"))
            try:
                minutes = int(request.POST.get("ttl_minutes") or MIN_INVITE_TTL_MINUTES)
            except ValueError:
                minutes = MIN_INVITE_TTL_MINUTES
            form["ttl_minutes"] = max(
                MIN_INVITE_TTL_MINUTES, min(MAX_INVITE_TTL_MINUTES, minutes),
            )

            scopes = "openid email profile" + (" roles" if form["roles"] else "")
            try:
                minted = mint(
                    expected_host=form["expected_host"],
                    provider_url=get_public_base_url(),
                    label=relying_party.name if relying_party else form["expected_host"],
                    scopes=scopes,
                    trusted=form["trusted"],
                    ttl=timedelta(minutes=form["ttl_minutes"]),
                    created_by=request.user,
                    relying_party=relying_party,
                )
            except EnrollmentError as exc:
                context["error"] = exc.message
            else:
                context["minted"] = {
                    "ticket": minted.ticket,
                    "qr": qr.render_data_uri(minted.ticket),
                    "expires_at": minted.invite.expires_at,
                    "seconds": minted.invite.seconds_remaining(),
                    "host": minted.invite.expected_host,
                    "scopes": minted.invite.granted_scopes,
                }

        return render(request, "admin/sso_master/pair.html", context)

    # -- displays -------------------------------------------------------------

    @admin.display(description="Origin")
    def origin(self, obj):
        return "paired" if obj.pairing_managed else "host config"

    @admin.display(description="Secret")
    def secret_state(self, obj):
        from django.utils import timezone

        if not obj.client_secret_hash:
            return "not set"
        bits = ["set"]
        if obj.secret_rotated_at:
            bits.append(f"rotated {obj.secret_rotated_at:%Y-%m-%d %H:%M}")
        if (
            obj.previous_secret_hash
            and obj.previous_secret_expires_at
            and timezone.now() < obj.previous_secret_expires_at
        ):
            note = f"previous valid until {obj.previous_secret_expires_at:%H:%M}"
            if obj.previous_secret_used_at:
                # The one thing neither side can otherwise see: the far end has
                # not picked up the new secret yet.
                note += " — the other side is still using it"
            bits.append(note)
        elif obj.secret_proven_at:
            bits.append(f"confirmed {obj.secret_proven_at:%Y-%m-%d %H:%M}")
        return " · ".join(bits)

    @admin.display(description="Pairing")
    def pairing_state(self, obj):
        if not obj.pairing_managed:
            return "Registered from host configuration (a sidecar, e.g. Gitea)."
        if obj.paired_at:
            return f"Paired {obj.paired_at:%Y-%m-%d %H:%M}."
        return "Invited, not yet redeemed."

    @admin.display(description="Last token")
    def last_seen(self, obj):
        """Is this federation actually being used?

        Derived from the newest access token rather than a new column, and it is
        the first time that question is answerable anywhere in the product.
        """
        latest = (
            obj.access_tokens.order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        return f"{latest:%Y-%m-%d %H:%M}" if latest else "never"


@admin.register(SSOFederationInvite)
class SSOFederationInviteAdmin(admin.ModelAdmin):
    """Read-only. Invites are born on the relying-party page, never here.

    Adding one here would produce an invite with no expected host — the single
    field that makes a stolen code useless.
    """

    list_display = [
        "expected_host", "state", "ticket_prefix", "granted_scopes",
        "attempt_count", "created_at", "expires_at",
    ]
    list_filter = ["granted_trusted", "created_at"]
    search_fields = ["expected_host", "ticket_prefix"]
    actions = ["revoke"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields] + ["state"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="State")
    def state(self, obj):
        return obj.state

    @admin.action(description="Revoke the selected pairing codes")
    def revoke(self, request, queryset):
        from django.utils import timezone

        count = queryset.filter(
            revoked_at__isnull=True, redeemed_at__isnull=True,
        ).update(revoked_at=timezone.now())
        self.message_user(request, f"Revoked {count} pairing code(s).")


@admin.register(SSOSigningKey)
class SSOSigningKeyAdmin(admin.ModelAdmin):
    list_display = ["key_id", "algorithm", "is_active", "created_at"]
    list_filter = ["is_active", "algorithm"]
    # is_active is readonly: deactivating the only active key makes
    # get_active_signing_key raise, and every token exchange 500s.
    readonly_fields = ["key_id", "algorithm", "public_key_pem", "created_at", "is_active"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SSOSubject)
class SSOSubjectAdmin(admin.ModelAdmin):
    """The subject is the ONLY thing a consumer matches an account on.

    Reassigning that OneToOne would hand one person's federated identity — and
    every account linked to it on every consumer — to somebody else. Read-only.
    """

    list_display = ["user", "subject", "created_at"]
    search_fields = ["user__username", "user__email", "subject"]
    readonly_fields = ["user", "subject", "created_at"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SSOAuthorizationCode)
class SSOAuthorizationCodeAdmin(admin.ModelAdmin):
    """Read-only and add-proof. See the module docstring for what add allowed."""

    list_display = [
        "client", "user", "code_fingerprint", "created_at", "expires_at", "used_at",
    ]
    list_filter = ["client", "created_at"]
    search_fields = ["user__username"]
    readonly_fields = [
        "code_fingerprint", "client", "user", "redirect_uri", "scope",
        "created_at", "expires_at", "used_at",
    ]
    exclude = ["code", "nonce", "state", "code_challenge", "code_challenge_method"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Code")
    def code_fingerprint(self, obj):
        return _fingerprint(obj.code)


@admin.register(SSOAccessToken)
class SSOAccessTokenAdmin(admin.ModelAdmin):
    """Read-only, add-proof, with a revoke action instead of editing."""

    list_display = [
        "client", "user", "token_fingerprint", "scope",
        "created_at", "expires_at", "revoked_at",
    ]
    list_filter = ["client", "created_at"]
    search_fields = ["user__username"]
    readonly_fields = [
        "token_fingerprint", "client", "user", "scope",
        "created_at", "expires_at", "revoked_at",
    ]
    exclude = ["token"]
    actions = ["revoke_tokens"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Token")
    def token_fingerprint(self, obj):
        return _fingerprint(obj.token)

    @admin.action(description="Revoke the selected access tokens")
    def revoke_tokens(self, request, queryset):
        from django.utils import timezone

        count = queryset.filter(revoked_at__isnull=True).update(
            revoked_at=timezone.now(),
        )
        self.message_user(request, f"Revoked {count} access token(s).")
