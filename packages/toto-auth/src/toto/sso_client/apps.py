import os

from django.apps import AppConfig


class SSOClientConfig(AppConfig):
    name = "toto.sso_client"
    verbose_name = "SSO Client (Consumer)"
    default_auto_field = "django.db.models.BigAutoField"

    def get_config(self):
        """
        Read OIDC consumer config from the active OIDCProviderConfig DB record.
        SSO_CLIENT_SECRET env var overrides the stored secret (production safety).
        """
        from toto.sso_client.models import OIDCProviderConfig
        record = OIDCProviderConfig.objects.filter(active=True).order_by("-imported_at").first()
        if not record:
            return {
                "portal_url": "", "client_id": "", "client_secret": "",
                "scopes": "openid email profile", "app_name": "",
                "trusted": False, "redirect_uris": [],
            }
        return {
            "portal_url": record.portal_url,
            "client_id": record.client_id,
            "client_secret": os.environ.get("SSO_CLIENT_SECRET") or record.client_secret,
            "scopes": record.scopes,
            "app_name": record.app_name,
            "trusted": record.trusted,
            "redirect_uris": record.redirect_uris_list(),
        }
