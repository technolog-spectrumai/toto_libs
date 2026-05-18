from django.db import models


class OIDCProviderConfig(models.Model):
    """
    Connection configuration for the upstream OIDC provider (portal).
    Populated via the admin import bundle view or management commands.
    """
    label = models.CharField(max_length=100, default="Portal")
    portal_url = models.URLField()
    client_id = models.CharField(max_length=128)
    client_secret = models.CharField(max_length=255, blank=True)
    scopes = models.CharField(max_length=255, default="openid email profile")
    active = models.BooleanField(default=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]
        verbose_name = "OIDC Provider Config"
        verbose_name_plural = "OIDC Provider Configs"

    def __str__(self):
        return f"{self.label} ({self.client_id})"
