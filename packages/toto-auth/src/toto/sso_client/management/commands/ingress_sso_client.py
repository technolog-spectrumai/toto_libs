"""
Seed an OIDCProviderConfig record for dev/testing.

Reads connection details from environment variables:
  SSO_PORTAL_URL      — URL of the portal acting as OIDC provider
  SSO_CLIENT_ID       — client_id registered on the portal
  SSO_CLIENT_SECRET   — client secret (stored in the record for dev; use env var in prod)
  SSO_APP_NAME        — optional display name (default: "SSO Client")
  SSO_REDIRECT_URIS   — optional comma-separated redirect URIs

In production, import a connection bundle via the admin instead.
"""
import os

from django.core.management.base import CommandError

from toto.ingress import IngressCommand
from toto.sso_client.models import OIDCProviderConfig


class Command(IngressCommand):
    help = "Seed OIDCProviderConfig from environment variables (dev/testing)."

    def process(self):
        portal_url = os.environ.get("SSO_PORTAL_URL", "").strip()
        client_id = os.environ.get("SSO_CLIENT_ID", "").strip()
        client_secret = os.environ.get("SSO_CLIENT_SECRET", "").strip()

        if not portal_url or not client_id:
            self.stdout.write(self.style.WARNING(
                "SSO_PORTAL_URL or SSO_CLIENT_ID not set — skipping OIDCProviderConfig seed."
            ))
            return

        app_name = os.environ.get("SSO_APP_NAME", "SSO Client")
        raw_uris = os.environ.get("SSO_REDIRECT_URIS", "")
        redirect_uris = "\n".join(u.strip() for u in raw_uris.split(",") if u.strip())

        OIDCProviderConfig.objects.filter(active=True).update(active=False)

        config, created = OIDCProviderConfig.objects.update_or_create(
            client_id=client_id,
            portal_url=portal_url,
            defaults={
                "label": f"{app_name} dev",
                "client_secret": client_secret,
                "scopes": "openid email profile",
                "app_name": app_name,
                "trusted": True,
                "redirect_uris": redirect_uris,
                "active": True,
            },
        )

        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} OIDCProviderConfig: {config.label} → {portal_url} (client_id={client_id})"
        ))
