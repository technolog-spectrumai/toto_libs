from django.core.management.base import BaseCommand

from toto.nomad import service


class Command(BaseCommand):
    help = "Ensure the faros onion is published (mint on first run, re-publish otherwise)."

    def handle(self, *args, **options):
        # Non-fatal by design: if the tor control port is unreachable we must not
        # block the web container from starting — the onion can be published on a
        # later boot. So we log and exit 0 rather than raising.
        try:
            onion = service.ensure_onion()
            self.stdout.write(self.style.SUCCESS(f"nomad: onion published -> {onion}.onion"))
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(f"nomad: ensure_onion failed (web will still start): {exc}")
