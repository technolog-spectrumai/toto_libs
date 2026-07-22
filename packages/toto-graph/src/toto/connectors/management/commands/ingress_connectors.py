"""Ingress for toto.connectors.

Always: ensure the archive bucket + (best-effort) the credentials strongbox.
--full: seed a working no-auth demo — OpenAlex works mapped onto the seeded
Bento ``note``/``concept`` templates with a ``references`` edge.
"""

from django.conf import settings
from django.contrib.auth import get_user_model

from toto.ingress import IngressCommand

DEMO_EXTRACT_CONFIG = {
    "endpoint": "works",
    "method": "GET",
    "params": {"per-page": 5, "filter": "from_publication_date:2025-01-01"},
    "records_path": "results",
    "pagination": {"strategy": "none"},
    "max_records": 10,
}

DEMO_MAPPING_SPEC = {
    "version": 1,
    "nodes": [
        {
            "rule_id": "work",
            "category_slug": "note",
            "identifier": {"path": "display_name"},
            "properties": {"name": {"path": "display_name"}},
            "when": {"path": "display_name", "op": "truthy"},
        },
        {
            "rule_id": "topic",
            "category_slug": "concept",
            "identifier": {"path": "primary_topic.display_name"},
            "properties": {"name": {"path": "primary_topic.display_name"}},
            "when": {"path": "primary_topic.display_name", "op": "truthy"},
        },
    ],
    "relationships": [
        {
            "rule_id": "work-references-topic",
            "edge_type_slug": "references",
            "from_rule": "work",
            "to_rule": "topic",
            "properties": {},
        }
    ],
}


class Command(IngressCommand):
    help = "Seed connectors infrastructure (+ an OpenAlex demo with --full)."

    def process(self):
        self.stdout.write(self.style.WARNING("Seeding Connectors..."))
        self._ensure_vault()
        self._ensure_archive_bucket()
        if self.full:
            self._seed_demo()
        self.stdout.write(self.style.SUCCESS("Connectors seeding complete."))

    def _ensure_vault(self):
        """Best-effort strongbox init (idempotent) so authenticated connectors
        can store secrets. No-op with a warning when the password is unset."""
        from django.core.management import call_command
        from django.core.management.base import CommandError

        if not (getattr(settings, "SABBIA_VAULT_PASSWORD", "") or "").strip():
            self.stdout.write(self.style.WARNING(
                "  SABBIA_VAULT_PASSWORD not set — skipping strongbox init "
                "(no-auth connectors still work; authenticated ones will not)."
            ))
            return
        try:
            call_command("connectors_init_vault", verbosity=0)
            self.stdout.write(self.style.SUCCESS("  Credentials strongbox ready."))
        except CommandError as exc:
            self.stdout.write(self.style.WARNING(f"  Strongbox init failed: {exc}"))

    def _ensure_archive_bucket(self):
        from ...services import archive

        try:
            bucket = archive.ensure_archive_bucket()
        except RuntimeError as exc:  # no superuser yet
            self.stdout.write(self.style.WARNING(f"  Archive bucket skipped: {exc}"))
            return
        self.stdout.write(self.style.SUCCESS(f"  Archive bucket ready: {bucket.slug}"))

    def _seed_demo(self):
        from toto.api.models import Connector

        from ...models import DataConnector

        owner = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()

        http_connector, created = Connector.objects.get_or_create(
            slug="openalex",
            defaults={
                "name": "OpenAlex",
                "provider": Connector.PROVIDER_GENERIC,
                "base_url": "https://api.openalex.org/",
                "auth_type": Connector.AUTH_NONE,
                "owner": owner,
            },
        )
        self.stdout.write(
            self.style.SUCCESS("  Created API connector: OpenAlex")
            if created
            else self.style.WARNING("  API connector exists: OpenAlex")
        )

        demo, created = DataConnector.objects.get_or_create(
            slug="openalex-demo",
            defaults={
                "name": "OpenAlex demo (works → notes/concepts)",
                "description": (
                    "Recent OpenAlex works become 'note' nodes referencing their "
                    "primary topic as a 'concept'. No auth; review before apply."
                ),
                "http_connector": http_connector,
                "extractor_kind": DataConnector.EXTRACTOR_REST_API,
                "extract_config": DEMO_EXTRACT_CONFIG,
                "mapping_spec": DEMO_MAPPING_SPEC,
                "trusted": False,
                "owner": owner,
            },
        )
        self.stdout.write(
            self.style.SUCCESS("  Created data connector: openalex-demo")
            if created
            else self.style.WARNING("  Data connector exists: openalex-demo")
        )
