"""
Seed the canonical UsageMetric rows for all integrated apps.

Usage:
    python manage.py seed_metering_metrics
"""
from django.core.management.base import BaseCommand

METRICS = [
    # Vault
    {"code": "storage.request",     "name": "Storage Request",     "namespace": "vault",   "default_unit": "request", "description": "One file upload/write request."},
    {"code": "storage.transfer_mb", "name": "Storage Transfer MB", "namespace": "vault",   "default_unit": "MB",      "description": "Megabytes uploaded or downloaded."},
    {"code": "storage.mb_hour",     "name": "Storage MB-Hour",     "namespace": "vault",   "default_unit": "MBh",     "description": "Megabyte-hours of storage consumed (snapshot)."},
    # VOD
    {"code": "vod.view",            "name": "VOD View",            "namespace": "vod",     "default_unit": "view",    "description": "A video playback session started (PLAY event)."},
    {"code": "vod.playback_second", "name": "VOD Playback Second", "namespace": "vod",     "default_unit": "second",  "description": "Seconds of video watched."},
    {"code": "vod.transfer_mb",     "name": "VOD Transfer MB",     "namespace": "vod",     "default_unit": "MB",      "description": "Megabytes of video/audio bytes delivered."},
    # Ravioli
    {"code": "ravioli.cypher_query","name": "Cypher Query",        "namespace": "ravioli", "default_unit": "query",   "description": "One Cypher query executed against Neo4j."},
    {"code": "ravioli.cypher_row",  "name": "Cypher Row",          "namespace": "ravioli", "default_unit": "row",     "description": "Rows/records returned by a Cypher query."},
    {"code": "ravioli.graph_event", "name": "Graph Change Event",  "namespace": "ravioli", "default_unit": "event",   "description": "One GraphChangeEvent processed from the outbox."},
    {"code": "ravioli.graph_write", "name": "Graph Write",         "namespace": "ravioli", "default_unit": "write",   "description": "Node or edge writes to the graph."},
    # Steven / AI
    {"code": "ai.prompt_token",     "name": "AI Prompt Token",     "namespace": "steven",  "default_unit": "token",   "description": "Input/prompt tokens consumed by an AI model call."},
    {"code": "ai.completion_token", "name": "AI Completion Token", "namespace": "steven",  "default_unit": "token",   "description": "Output/completion tokens produced by an AI model call."},
    {"code": "ai.total_token",      "name": "AI Total Token",      "namespace": "steven",  "default_unit": "token",   "description": "Total tokens (prompt + completion) for a model call."},
    {"code": "ai.agent_run",        "name": "AI Agent Run",        "namespace": "steven",  "default_unit": "run",     "description": "One complete agent run execution."},
    {"code": "ai.tool_call",        "name": "AI Tool Call",        "namespace": "steven",  "default_unit": "call",    "description": "One tool invocation during an agent run."},
]


class Command(BaseCommand):
    help = "Seed canonical UsageMetric rows for vault, vod, ravioli, and steven."

    def handle(self, *args, **options):
        from toto.metering.models import UsageMetric

        created = updated = skipped = 0
        for spec in METRICS:
            obj, was_created = UsageMetric.objects.get_or_create(
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "namespace": spec["namespace"],
                    "default_unit": spec["default_unit"],
                    "description": spec["description"],
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  + {spec['code']}"))
            else:
                changed = False
                for field in ("name", "namespace", "default_unit", "description"):
                    if getattr(obj, field) != spec[field]:
                        setattr(obj, field, spec[field])
                        changed = True
                if changed:
                    obj.save(update_fields=["name", "namespace", "default_unit", "description", "updated_at"])
                    updated += 1
                    self.stdout.write(self.style.WARNING(f"  ~ {spec['code']} (updated)"))
                else:
                    skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created} created, {updated} updated, {skipped} unchanged."
            )
        )
