from django.apps import AppConfig


class SSOAuthConfig(AppConfig):
    name = "toto.sso_client"
    verbose_name = "SSO Auth (Consumer)"
    default_auto_field = "django.db.models.BigAutoField"

    def get_config(self):
        """
        Return the active OIDC consumer config.
        Priority: env vars > active OIDCProviderConfig DB record > OIDC_AUTH_CONFIG settings > sso.yaml
        """
        import os
        from django.conf import settings

        cfg = getattr(settings, "OIDC_AUTH_CONFIG", {})
        # Try DB record
        db_record = None
        try:
            from toto.sso_client.models import OIDCProviderConfig
            db_record = OIDCProviderConfig.objects.filter(active=True).order_by("-imported_at").first()
        except Exception:
            pass

        return {
            "portal_url": os.environ.get("SSO_PORTAL_URL") or cfg.get("portal_url") or (db_record.portal_url if db_record else ""),
            "client_id": os.environ.get("SSO_CLIENT_ID") or cfg.get("client_id") or (db_record.client_id if db_record else ""),
            "client_secret": os.environ.get("SSO_CLIENT_SECRET") or (db_record.client_secret if db_record else cfg.get("client_secret", "")),
            "scopes": cfg.get("scopes") or (db_record.scopes if db_record else "openid email profile"),
            "app_name": cfg.get("app_name", ""),
            "trusted": cfg.get("trusted", False),
            "redirect_uris": cfg.get("redirect_uris", []),
        }
