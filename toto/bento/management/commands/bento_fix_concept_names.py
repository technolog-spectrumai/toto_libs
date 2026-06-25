"""Repair / clean stray ingestor-created Concept/Note nodes.

The original concept/note schema declared ``title``, so ingestor-created nodes
stored their name in ``extra`` and left the identifier empty (which displayed as a
uid and tripped "required" validation). After the schema fix (``name``/``body``)
this command finds those strays and either migrates the buried name to the
top-level ``name`` property, or deletes them. Read-only by default.

    manage.py bento_fix_concept_names                # inspect (dry run)
    manage.py bento_fix_concept_names --migrate      # extra.name -> name
    manage.py bento_fix_concept_names --delete       # remove the strays
    manage.py bento_fix_concept_names --categories concept note
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Inspect / migrate / delete stray Concept/Note nodes whose name is buried in `extra`."

    def add_arguments(self, parser):
        parser.add_argument("--migrate", action="store_true", help="Move extra.name → top-level name.")
        parser.add_argument("--delete", action="store_true", help="Delete the stray nodes.")
        parser.add_argument(
            "--categories", nargs="+", default=["concept", "note"],
            help="Category slugs to scan (default: concept note).",
        )

    def handle(self, *args, **opts):
        from toto.bento import graph_service as gs
        from toto.ravioli.connection import is_enabled

        if opts["migrate"] and opts["delete"]:
            raise CommandError("Choose only one of --migrate / --delete.")
        if not is_enabled():
            raise CommandError("RAVIOLI_ENABLED is False — Neo4j is not available.")

        strays = []  # (slug, uid, buried_name)
        for slug in opts["categories"]:
            for node in gs.iter_nodes(cat_slug=slug):
                props = node.get("properties") or {}
                extra = props.get("extra")
                extra = extra if isinstance(extra, dict) else {}
                buried = extra.get("name") or extra.get("title")
                if not props.get("name") and buried:
                    strays.append((slug, node["uid"], buried))

        if not strays:
            self.stdout.write(self.style.SUCCESS("✅ No stray Concept/Note nodes found."))
            return

        self.stdout.write(self.style.WARNING(f"Found {len(strays)} stray node(s):"))
        for slug, uid, buried in strays:
            self.stdout.write(f"  - [{slug}] {uid}  name←“{buried}”")

        if opts["migrate"]:
            for _slug, uid, buried in strays:
                gs.update_node(uid, {"name": buried})
            self.stdout.write(self.style.SUCCESS(
                f"🛠️ Migrated {len(strays)} node(s): name set top-level "
                "(the leftover extra.name copy is harmless)."
            ))
        elif opts["delete"]:
            gs.delete_nodes([uid for _slug, uid, _buried in strays])
            self.stdout.write(self.style.SUCCESS(f"🗑️ Deleted {len(strays)} node(s)."))
        else:
            self.stdout.write(self.style.NOTICE("Dry run — pass --migrate or --delete to act."))
