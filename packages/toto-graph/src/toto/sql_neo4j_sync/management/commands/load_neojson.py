"""Load a NeoJSON document into Neo4j via ravioli's sync engine.

    python manage.py load_neojson path/to/graph.neojson --mode merge
    python manage.py load_neojson path/to/graph.neojson --mode replace

`merge` upserts the document; `replace` makes the ravioli-owned graph match it
(deleting managed nodes/relationships the document doesn't contain).
"""

from django.core.management.base import BaseCommand, CommandError

from toto.ravioli import neojson
from toto.ravioli.connection import Neo4jClient, is_enabled
from toto.sql_neo4j_sync import graphsync


class Command(BaseCommand):
    help = "Load a .neojson document into Neo4j (merge or replace)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to a .neojson file.")
        parser.add_argument(
            "--mode",
            choices=list(graphsync.MODES),
            default=graphsync.MODE_MERGE,
            help="merge (upsert, default) or replace (match the document, deleting extras).",
        )

    def handle(self, *args, **options):
        if not is_enabled():
            raise CommandError("RAVIOLI_ENABLED is False — cannot connect to Neo4j.")

        path = options["path"]
        mode = options["mode"]

        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            raise CommandError(f"Could not read {path!r}: {exc}")

        try:
            graph = neojson.loads(text)
        except neojson.NeoJsonParseError as exc:
            raise CommandError(f"Invalid NeoJSON: {exc}")
        errors = neojson.validate(graph)
        if errors:
            raise CommandError("Invalid NeoJSON: " + "; ".join(errors))

        client = Neo4jClient()
        try:
            summary = graphsync.load_neojson(client, graph, mode=mode)
        finally:
            client.close()

        n = summary.get("nodes", {})
        r = summary.get("relationships", {})
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {path} into the graph ({mode}). "
                f"Nodes +{n.get('create', 0)} ~{n.get('update', 0)} -{n.get('delete', 0)}; "
                f"Edges +{r.get('create', 0)} ~{r.get('update', 0)} -{r.get('delete', 0)}."
            )
        )
