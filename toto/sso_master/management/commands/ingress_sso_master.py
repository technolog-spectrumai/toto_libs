import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from toto.ingress import IngressCommand

from ...models import SSOSigningKey
from ...provisioning import create_relying_party
from ...services import get_public_base_url


class Command(IngressCommand):
    help = "Seed dev SSO users for the portal and provision built-in relying parties."

    def process(self):
        self._ensure_signing_key()
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
        self._provision_gitea()

    def _ensure_signing_key(self):
        """ID tokens are RS256-signed with the active SSOSigningKey (private half
        encrypted in Gervazy). Nothing else creates it, so on a fresh DB (first
        deploy, RESET=1 wipe) every OIDC token exchange would 500 until someone
        ran create_sso_signing_key by hand — burning the single-use code, so the
        relying party's retry surfaced only as a cryptic invalid_grant. Ensure a
        key here instead; runs at most once per DB lifetime."""
        if SSOSigningKey.objects.filter(is_active=True).exists():
            return
        if not (getattr(settings, "SSO_VAULT_PASSWORD", "") or "").strip():
            self.stdout.write(self.style.WARNING(
                "No active SSOSigningKey and SSO_VAULT_PASSWORD is unset — "
                "skipping key creation; OIDC logins will fail until one exists."
            ))
            return
        owner = (
            get_user_model().objects
            .filter(is_superuser=True, is_active=True)
            .order_by("pk")
            .first()
        )
        if owner is None:
            self.stdout.write(self.style.WARNING(
                "No active SSOSigningKey and no superuser to own the vault — "
                "skipping key creation; OIDC logins will fail until one exists."
            ))
            return
        key_id = f"sso-auto-{timezone.now():%Y%m%d-%H%M%S}"
        call_command(
            "create_sso_signing_key",
            key_id=key_id,
            vault_owner=owner.username,
            stdout=self.stdout,
        )

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

        base = get_public_base_url()
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

    def _provision_gitea(self):
        """Register Gitea as a trusted OIDC relying party so it can SSO against the
        portal. Idempotent like the Grafana one above. Staff-only access is enforced
        on the Gitea side: its OAuth source requires `staff` in the `roles` claim and
        maps `admin` → instance admin (see portal/deploy/gitea/provision_oauth.sh).
        The redirect URI embeds the Gitea auth-source name — GITEA_OIDC_SOURCE_NAME
        must match what that script creates (both default to "portal-sso")."""
        if not getattr(settings, "GITEA_ENABLED", False):
            return
        secret = getattr(settings, "GITEA_OIDC_CLIENT_SECRET", "") or ""
        if not secret:
            self.stdout.write(self.style.WARNING(
                "GITEA_ENABLED but GITEA_OIDC_CLIENT_SECRET is empty — "
                "skipping Gitea relying-party provisioning."
            ))
            return

        base = get_public_base_url()
        if not base:
            self.stdout.write(self.style.WARNING(
                "PLATFORM_DOMAIN is empty — cannot build Gitea redirect URI; skipping."
            ))
            return

        source = getattr(settings, "GITEA_OIDC_SOURCE_NAME", "portal-sso")
        redirect_uri = f"{base}/gitea/user/oauth2/{source}/callback"
        create_relying_party(
            name="Gitea",
            client_id="gitea",
            trusted=True,  # skip the consent screen for seamless SSO
            redirect_uris=[redirect_uri],
            scopes="openid email profile roles",
            raw_secret=secret,
            force_recreate=True,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Provisioned Gitea OIDC relying party (redirect {redirect_uri})"
        ))
