"""Mint this host's monetary issuer keypair. Run once, on the master only.

Deliberately a management command and never a migration: a migration runs on
every host that migrates, so minting there would manufacture a monetary master
on each one. Issuing authority is granted by an operator, once, on purpose.
"""

from django.core.management.base import BaseCommand, CommandError

from toto.assets import issuer as issuer_module
from toto.assets.issuer import ISSUER_KEY_SETTING, NotTheMaster


class Command(BaseCommand):
    help = ("Create the monetary issuer keypair for this platform. "
            "The master runs this once; a branch never runs it.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--label", required=True,
            help="Who this authority is, e.g. the platform name.")

    def handle(self, *args, **options):
        try:
            issuer = issuer_module.mint_issuer(label=options["label"])
        except NotTheMaster as refusal:
            raise CommandError(str(refusal)) from refusal

        self.stdout.write(self.style.SUCCESS(
            f"monetary issuer {issuer.fingerprint[:12]}… minted for "
            f"“{issuer.label}”"))
        self.stdout.write(
            f"  the private half is sealed under {ISSUER_KEY_SETTING}; keep "
            "that secret OUT of backups, or a restored copy becomes a second "
            "master")
        self.stdout.write("  fingerprint: " + issuer.fingerprint)
