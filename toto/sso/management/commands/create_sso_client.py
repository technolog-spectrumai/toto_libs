import secrets

from django.core.management.base import BaseCommand, CommandError

from toto.sso.models import SSOClient


class Command(BaseCommand):
    help = "Create an SSO/OIDC client that can use this server for login."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--redirect-uri", action="append", required=True, help="Can be provided multiple times.")
        parser.add_argument("--trusted", action="store_true", help="Skip consent for this client.")
        parser.add_argument("--public", action="store_true", help="Create a public client. Public clients must use PKCE.")
        parser.add_argument("--scopes", default="openid email profile")
        parser.add_argument("--client-id", default=None)

    def handle(self, *args, **options):
        client_id = options["client_id"] or secrets.token_urlsafe(24)
        if SSOClient.objects.filter(client_id=client_id).exists():
            raise CommandError(f"Client ID already exists: {client_id}")

        client = SSOClient(
            name=options["name"],
            client_id=client_id,
            redirect_uris="\n".join(options["redirect_uri"]),
            trusted=options["trusted"],
            allowed_scopes=options["scopes"],
            client_type=SSOClient.PUBLIC if options["public"] else SSOClient.CONFIDENTIAL,
        )

        raw_secret = None
        if client.client_type == SSOClient.CONFIDENTIAL:
            raw_secret = client.set_client_secret()

        client.save()

        self.stdout.write(self.style.SUCCESS("SSO client created"))
        self.stdout.write(f"client_id: {client.client_id}")
        if raw_secret:
            self.stdout.write(f"client_secret: {raw_secret}")
            self.stdout.write(self.style.WARNING("Store this secret now. It is hashed and cannot be shown again."))
        else:
            self.stdout.write("client_secret: none, public client; use PKCE")
