"""Generate the gervazy self-signed TLS cert from inside the container.

deploy.py normally creates the cert on the host before bringing the stack up, but
that needs the `cryptography` package in the host Python. On a bare server checkout
that dep is absent, so the cert generation is deferred to here: the web image
bundles `cryptography` + toto and mounts the shared cert dir read-write at
/app/cert, while nginx mounts the same dir read-only and waits for web to become
healthy. Running this in the web entrypoint (before the server starts) therefore
guarantees the cert exists before nginx ever starts.

Idempotent: skips when both cert and key already exist (e.g. pre-seeded via push).
Cert parameters come from the SSL_CERT_* env vars that deploy.py writes into .env.
"""

import os

from django.core.management.base import BaseCommand

from toto.gervazy.crypto import ensure_self_signed_certificate


def _split(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


class Command(BaseCommand):
    help = "Generate the gervazy self-signed TLS cert (for ssl.mode: gervazy)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cert-path",
            default=os.environ.get("SSL_CERT_PATH", "/app/cert/localhost.crt"),
        )
        parser.add_argument(
            "--key-path",
            default=os.environ.get("SSL_KEY_PATH", "/app/cert/localhost.key"),
        )
        parser.add_argument(
            "--common-name",
            default=os.environ.get("SSL_CERT_COMMON_NAME", "localhost"),
        )
        parser.add_argument(
            "--dns-names",
            default=os.environ.get("SSL_CERT_DNS_NAMES", "localhost"),
            help="Comma-separated DNS SANs.",
        )
        parser.add_argument(
            "--ip-addresses",
            default=os.environ.get("SSL_CERT_IP_ADDRESSES", "127.0.0.1"),
            help="Comma-separated IP SANs.",
        )

    def handle(self, *args, **options):
        cert_path = options["cert_path"]
        key_path = options["key_path"]
        dns_names = _split(options["dns_names"]) or ["localhost"]
        ip_addresses = _split(options["ip_addresses"]) or ["127.0.0.1"]

        created = ensure_self_signed_certificate(
            cert_path,
            key_path,
            common_name=options["common_name"],
            dns_names=dns_names,
            ip_addresses=ip_addresses,
        )

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created gervazy self-signed cert {cert_path} "
                    f"(CN={options['common_name']}, DNS={dns_names}, IP={ip_addresses})"
                )
            )
        else:
            self.stdout.write(f"gervazy cert already exists at {cert_path} — leaving as-is")
