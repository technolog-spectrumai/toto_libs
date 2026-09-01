"""Seed the email ACCOUNT from the deploy config — never its password.

The builder can pre-configure the address (JESS_EMAIL_HOST / PORT / USER /
FROM / TLS); those are ordinary transport facts and belong in config the same
way DB_HOST does. This command turns them into the one EmailProvider row the
deploy owns, idempotently, on every boot (entrypoint.sh runs ingress_all).

The password deliberately never passes through here. Its custody is decided
elsewhere: stored encrypted via /jess/account/, typed per release (manual
mode), or held in process memory per login session (/jess/unlock/, with the
optional JESS_EMAIL_PASSWORD bootstrap read by toto.jess.credentials — not by
this command, and never written to this row).
"""
import os

from toto.ingress import IngressCommand

from toto.jess.models import BACKEND_SMTP, EmailProvider

#: The label marking the row this command owns. An operator's own rows are
#: never touched; this one is rewritten from env on every boot.
DEPLOY_LABEL = "Deploy config"


class Command(IngressCommand):
    help = "Seed the deploy-configured email provider from JESS_EMAIL_* env"

    bootstrap_economy = False

    def process(self):
        host = (os.environ.get("JESS_EMAIL_HOST") or "").strip()
        user = (os.environ.get("JESS_EMAIL_USER") or "").strip()
        if not host or not user:
            # Nothing pre-configured. Leave whatever the operator built in
            # the app alone — including deleting nothing this command made
            # earlier: a removed env key must not tear down a provider that
            # meanwhile became the active one somebody relies on.
            self.stdout.write("jess: no JESS_EMAIL_HOST/USER — nothing to seed.")
            return

        values = {
            "backend": BACKEND_SMTP,
            "host": host,
            "port": int(os.environ.get("JESS_EMAIL_PORT", "587") or 587),
            "use_tls": (os.environ.get("JESS_EMAIL_TLS", "1") == "1"),
            "use_ssl": (os.environ.get("JESS_EMAIL_SSL", "0") == "1"),
            "username": user,
            "from_address": (os.environ.get("JESS_EMAIL_FROM") or "").strip(),
        }
        # Not update_or_create: label is not unique, and an operator who made
        # their own "Deploy config" row by hand would turn that into a
        # MultipleObjectsReturned that fails ingress_all — i.e. the boot.
        # Oldest row wins and is treated as ours.
        provider = (
            EmailProvider.objects.filter(label=DEPLOY_LABEL)
            .order_by("pk").first()
        )
        created = provider is None
        if created:
            provider = EmailProvider(label=DEPLOY_LABEL)
        for field, value in values.items():
            setattr(provider, field, value)
        # Activate only on FIRST seed, and only when nothing else is active.
        # Never on a later boot: an operator who deliberately deactivated
        # every provider (mail off, resets on the patron flow) must not find
        # it re-armed each morning — and never a coup against a provider they
        # switched to on purpose.
        if created and not EmailProvider.objects.filter(active=True).exists():
            provider.active = True
        provider.save()
        self.stdout.write(
            f"jess: {'created' if created else 'updated'} '{DEPLOY_LABEL}' "
            f"({user}@{host}, password custody separate)."
        )
