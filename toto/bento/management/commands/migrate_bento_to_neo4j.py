"""Migrate legacy SQL bento content (IdeaBox/IdeaLink) into Neo4j.

Bento used to store nodes/edges in SQL (tables ``bento_ideabox`` /
``bento_idealink``). Those models are gone; the data — if any — now belongs in
Neo4j. This command reads the *legacy tables directly via raw SQL* (so it works
even though the Django models no longer exist) and recreates the content through
:mod:`toto.bento.graph_service`.

Because the old free-text categories/labels don't map 1:1 onto the new
templates, an actual (non-dry-run) migration requires you to name a target
node category and edge type:

    python manage.py migrate_bento_to_neo4j --dry-run
    python manage.py migrate_bento_to_neo4j --category idea --edge-type related

If the legacy tables don't exist, the command is a no-op. It never deletes
anything.
"""

import json

from django.core.management.base import BaseCommand
from django.db import connection


LEGACY_NODE_TABLE = "bento_ideabox"
LEGACY_EDGE_TABLE = "bento_idealink"


class Command(BaseCommand):
    help = "Migrate legacy SQL bento content (IdeaBox/IdeaLink) into Neo4j."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
        parser.add_argument("--category", help="Target BentoCategory slug for migrated nodes.")
        parser.add_argument("--edge-type", help="Target BentoEdgeType slug for migrated edges.")

    def handle(self, *args, **options):
        tables = set(connection.introspection.table_names())
        if LEGACY_NODE_TABLE not in tables:
            self.stdout.write(self.style.WARNING(
                f"No legacy table '{LEGACY_NODE_TABLE}' found — nothing to migrate."
            ))
            return

        nodes = self._rows(LEGACY_NODE_TABLE, ("id", "label", "properties"))
        edges = (
            self._rows(LEGACY_EDGE_TABLE, ("from_box_id", "to_box_id", "label", "properties"))
            if LEGACY_EDGE_TABLE in tables else []
        )
        self.stdout.write(f"Found {len(nodes)} legacy node(s) and {len(edges)} legacy edge(s).")

        dry_run = options["dry_run"]
        cat_slug = options["category"]
        et_slug = options["edge_type"]

        if dry_run:
            self.stdout.write(self.style.NOTICE(
                "Dry run — would migrate IdeaBox→nodes and IdeaLink→edges. "
                "Re-run with --category <slug> --edge-type <slug> to write to Neo4j."
            ))
            return

        if not cat_slug:
            # Don't guess a mapping; leave a clear, documented stop.
            self.stdout.write(self.style.ERROR(
                "Refusing to migrate without --category <slug> (and --edge-type <slug> "
                "for edges). The legacy free-text categories do not map onto the new "
                "templates automatically. Nothing was changed."
            ))
            return

        from toto.bento import graph_service as gs

        id_to_uid = {}
        created_nodes = 0
        for row in nodes:
            props = {"label": row["label"], **self._json(row["properties"])}
            node = gs.create_node(cat_slug, props)
            id_to_uid[row["id"]] = node["uid"]
            created_nodes += 1
        self.stdout.write(self.style.SUCCESS(f"Created {created_nodes} node(s) in Neo4j."))

        created_edges = 0
        if edges and et_slug:
            for row in edges:
                src = id_to_uid.get(row["from_box_id"])
                tgt = id_to_uid.get(row["to_box_id"])
                if not (src and tgt):
                    continue
                gs.create_edge(et_slug, src, tgt, self._json(row["properties"]))
                created_edges += 1
            self.stdout.write(self.style.SUCCESS(f"Created {created_edges} edge(s) in Neo4j."))
        elif edges:
            self.stdout.write(self.style.WARNING(
                f"Skipped {len(edges)} edge(s) — pass --edge-type <slug> to migrate them."
            ))

        self.stdout.write(self.style.SUCCESS("Done. Legacy SQL rows were left untouched."))

    @staticmethod
    def _rows(table, columns):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {', '.join(columns)} FROM {table}")
            return [dict(zip(columns, r)) for r in cursor.fetchall()]

    @staticmethod
    def _json(value):
        if isinstance(value, dict):
            return value
        try:
            data = json.loads(value or "{}")
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}
