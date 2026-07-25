from django.core.management.base import BaseCommand, CommandError

from toto.sso_master.provisioning import (
    RelyingPartyProvisioningError,
    add_relying_party_arguments,
    create_relying_party_from_options,
)


class Command(BaseCommand):
    help = "Create an SSO/OIDC relying party that can use this server for login."

    def add_arguments(self, parser):
        add_relying_party_arguments(parser)

    def handle(self, *args, **options):
        try:
            provisioned = create_relying_party_from_options(options)
        except RelyingPartyProvisioningError as exc:
            raise CommandError(str(exc)) from exc

        relying_party = provisioned.relying_party
        self.stdout.write(self.style.SUCCESS("SSO relying party created"))
        self.stdout.write(f"client_id: {relying_party.client_id}")
        if provisioned.client_secret:
            self.stdout.write(f"client_secret: {provisioned.client_secret}")
            self.stdout.write(self.style.WARNING("Store this secret now. It is hashed and cannot be shown again."))
        else:
            self.stdout.write("client_secret: none, public relying party; use PKCE")
