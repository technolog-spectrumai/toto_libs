from django.db import models


class OIDCProviderConfig(models.Model):
    """
    Single source of truth for OIDC consumer configuration.
    Only one record should be active at a time.
    Populated via admin (import connection bundle) or ingress_sso_client (dev).
    """
    label = models.CharField(max_length=100, default="Portal")
    portal_url = models.URLField()
    client_id = models.CharField(max_length=128)
    client_secret = models.CharField(max_length=255, blank=True)
    scopes = models.CharField(max_length=255, default="openid email profile")
    # Manifest export fields
    app_name = models.CharField(max_length=100, default="")
    trusted = models.BooleanField(default=False)
    redirect_uris = models.TextField(blank=True, help_text="One redirect URI per line.")
    active = models.BooleanField(default=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]
        verbose_name = "OIDC Provider Config"
        verbose_name_plural = "OIDC Provider Configs"

    def __str__(self):
        return f"{self.label} ({self.client_id})"

    def redirect_uris_list(self):
        return [u.strip() for u in self.redirect_uris.splitlines() if u.strip()]
