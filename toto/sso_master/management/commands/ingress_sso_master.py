import os

from django.conf import settings
from django.contrib.auth import get_user_model

from toto.ingress import IngressCommand

from ...provisioning import create_relying_party


def _platform_base_url() -> str:
    """Browser-facing base URL for the portal, used to build OIDC redirect URIs."""
    domain = (getattr(settings, "PLATFORM_DOMAIN", "") or "").strip().rstrip("/")
    if not domain:
        return ""
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


class Command(IngressCommand):
    help = "Seed dev SSO users for the portal and provision built-in relying parties."

    def process(self):
        User = get_user_model()
        users = [
            {"username": "sso1", "password": os.environ.get("SSO1_PASSWORD", "sso1")},
        ]
        for spec in users:
            user, created = User.objects.update_or_create(
                username=spec["username"],
                defaults={},
            )
            user.set_password(spec["password"])
            user.save(update_fields=["password"])
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb} user: {user.username}"))

        self._provision_grafana()

    def _provision_grafana(self):
        """Register Grafana as a trusted OIDC relying party so it can SSO against
        the portal. Idempotent (force_recreate) so redeploys / RESET=1 re-seed a
        stable client_id + the deployment's shared secret. Superuser-only access is
        enforced on the Grafana side via strict role mapping over the `roles` claim."""
        if not getattr(settings, "GRAFANA_ENABLED", False):
            return
        secret = getattr(settings, "GRAFANA_OIDC_CLIENT_SECRET", "") or ""
        if not secret:
            self.stdout.write(self.style.WARNING(
                "GRAFANA_ENABLED but GRAFANA_OIDC_CLIENT_SECRET is empty — "
                "skipping Grafana relying-party provisioning."
            ))
            return

        base = _platform_base_url()
        if not base:
            self.stdout.write(self.style.WARNING(
                "PLATFORM_DOMAIN is empty — cannot build Grafana redirect URI; skipping."
            ))
            return

        create_relying_party(
            name="Grafana",
            client_id="grafana",
            trusted=True,  # skip the consent screen for seamless SSO
            redirect_uris=[f"{base}/grafana/login/generic_oauth"],
            scopes="openid email profile roles",
            raw_secret=secret,
            force_recreate=True,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Provisioned Grafana OIDC relying party (redirect {base}/grafana/login/generic_oauth)"
        ))
