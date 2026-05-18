from django.apps import AppConfig


class OidcAuthConfig(AppConfig):
    name = "toto.oidc_auth"
    verbose_name = "OIDC Authentication (consumer)"

    def get_config(self):
        """Return the OIDC_AUTH_CONFIG dict from settings, with defaults."""
        from django.conf import settings
        cfg = getattr(settings, "OIDC_AUTH_CONFIG", {})
        return {
            "portal_url": cfg.get("portal_url", ""),
            "client_id": cfg.get("client_id", ""),
            "client_secret": cfg.get("client_secret", ""),
            "scopes": cfg.get("scopes", "openid email profile"),
            "app_name": cfg.get("app_name", ""),
            "trusted": cfg.get("trusted", False),
            "redirect_uris": cfg.get("redirect_uris", []),
        }
