from django.contrib import admin
from django.utils.html import format_html
from gate.models import Challenge, RefreshToken
from toto.batch import BatchAction


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "masked_nonce",
        "issued_at",
        "expires_at",
        "status_display",
    )
    list_filter = ("verified", "issued_at", "expires_at")
    search_fields = ("nonce", "identity__id", "identity__name")
    readonly_fields = ("issued_at", "expires_at", "verified", "masked_nonce")
    actions = ["solve_challenges"]

    def masked_nonce(self, obj):
        """
        Show a masked version of the nonce so it isn't exposed in plain text.
        """
        return "*" * len(obj.nonce) if obj.nonce else ""
    masked_nonce.short_description = "Nonce (masked)"

    def status_display(self, obj):
        """
        Color-coded status for quick visual feedback.
        """
        if obj.verified:
            return format_html('<span style="color:green;">Verified ✅</span>')
        elif obj.is_expired():
            return format_html('<span style="color:red;">Expired ❌</span>')
        return format_html('<span style="color:orange;">Pending ⏳</span>')
    status_display.short_description = "Status"

    @admin.action(description="Solve selected challenges")
    def solve_challenges(self, request, queryset):
        def solve_one(challenge):
            if not challenge.is_expired() and not challenge.verified:
                rsa_pair = getattr(challenge.identity, "rsa_keypair", None)
                if rsa_pair:
                    # Sign the nonce with the identity’s RSA private key
                    signature = rsa_pair.sign(challenge.nonce.encode())
                    # Verify the challenge
                    result = challenge.verify(signature)
                    # Attach the signature to the object for display
                    challenge._solution_signature = signature
                    return (challenge, result)
            return (challenge, False)

        results = BatchAction(queryset).run(solve_one)

        # Display messages with signature included
        for challenge, result in results.success:
            if hasattr(challenge, "_solution_signature"):
                sig = challenge._solution_signature.hex()
                msg = f"Challenge {challenge.id} solved ✅ (signature = {sig})"
            else:
                msg = f"Challenge {challenge.id} could not be solved ❌"
            self.message_user(request, msg)

@admin.register(RefreshToken)
class RefreshTokenAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "short_token",
        "issued_at",
        "expires_at",
        "revoked",
        "status_display",
    )
    list_filter = ("revoked", "issued_at", "expires_at")
    search_fields = ("token", "user__username", "user__email", "user__id")
    readonly_fields = ("issued_at", "short_token")

    actions = ["revoke_tokens"]

    def short_token(self, obj):
        """
        Show a shortened version of the token for readability.
        """
        return obj.token[:12] + "..." if obj.token else ""
    short_token.short_description = "Token (short)"

    def status_display(self, obj):
        """
        Color-coded status for quick visual feedback.
        """
        if obj.revoked:
            return format_html('<span style="color:red;">Revoked ❌</span>')
        elif obj.is_expired():
            return format_html('<span style="color:orange;">Expired ⏳</span>')
        return format_html('<span style="color:green;">Active ✅</span>')
    status_display.short_description = "Status"

    def revoke_tokens(self, request, queryset):
        """
        Admin action to revoke selected tokens.
        """
        count = queryset.update(revoked=True)
        self.message_user(request, f"{count} token(s) revoked.")
    revoke_tokens.short_description = "Revoke selected tokens"
