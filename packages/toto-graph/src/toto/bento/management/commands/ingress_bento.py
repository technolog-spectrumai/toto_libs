import random
import re

from django.conf import settings
from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.bento.models import BentoCategory, BentoEdgeType, RESERVED_PROP_NAMES


# Django field → bento property type (bento types: string/text/integer/float/
# boolean/datetime/json). YAML transforms override the model lookup.
_FIELD_TYPE = {
    "CharField": "string", "SlugField": "string", "EmailField": "string",
    "URLField": "string", "UUIDField": "string", "GenericIPAddressField": "string",
    "TextField": "text",
    "IntegerField": "integer", "PositiveIntegerField": "integer",
    "PositiveSmallIntegerField": "integer", "SmallIntegerField": "integer",
    "BigIntegerField": "integer", "AutoField": "integer", "BigAutoField": "integer",
    "FloatField": "float", "DecimalField": "float",
    "BooleanField": "boolean", "NullBooleanField": "boolean",
    "DateTimeField": "datetime", "DateField": "datetime", "TimeField": "datetime",
    "JSONField": "json",
}


class Command(IngressCommand):
    help = "Seed Bento templates (synced graph types always; demo data with --full)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        # Seed the minimal concept/note/references graph types. Tri-state like
        # --rich: default None falls back to settings.SEED_GRAPH_TYPES.
        parser.add_argument(
            "--seed-graph-types",
            dest="seed_graph_types",
            action="store_true",
            default=None,
            help="Seed the 'concept'/'note' categories + 'references' edge type "
                 "(even in non-full mode). Default: settings.SEED_GRAPH_TYPES.",
        )

    def handle(self, *args, **options):
        opt = options.get("seed_graph_types", None)
        self.seed_graph_types = (
            getattr(settings, "SEED_GRAPH_TYPES", False) if opt is None else opt
        )
        super().handle(*args, **options)

    def process(self):
        # Always: node categories + edge types mirroring the SQL→Neo4j sync
        # configs, so synced labels/relations map to bento templates and are
        # recognised when the sync writes them.
        self._seed_synced_templates()

        # Minimal graph types (concept/note/references) — gated by the flag but
        # seeded in every mode (including thin/non-full) to make testing easy.
        if self.seed_graph_types:
            self._seed_graph_types()

        if not self.full:
            return

        # Full mode also seeds the demo "idea / source / question" templates that
        # back the sample graph seeded below.
        self.stdout.write(self.style.WARNING("🍱 Seeding Bento demo templates…"))

        categories = {
            "idea": {
                "name": "Idea",
                "neo4j_label": "Idea",
                "description": "A reusable idea, principle, or insight.",
                "property_schema": [
                    {"name": "title", "type": "string", "required": True, "label": "Title"},
                    {"name": "body", "type": "text", "required": False, "label": "Body"},
                    {"name": "rating", "type": "integer", "required": False, "label": "Rating"},
                ],
            },
            "source": {
                "name": "Source",
                "neo4j_label": "Source",
                "description": "A book, article, or note an idea came from.",
                "property_schema": [
                    {"name": "title", "type": "string", "required": True, "label": "Title"},
                    {"name": "url", "type": "string", "required": False, "label": "URL"},
                    {"name": "kind", "type": "string", "required": False, "label": "Kind"},
                ],
            },
            "question": {
                "name": "Question",
                "neo4j_label": "Question",
                "description": "An open question worth returning to.",
                "property_schema": [
                    {"name": "title", "type": "string", "required": True, "label": "Question"},
                    {"name": "status", "type": "string", "required": False, "label": "Status"},
                ],
            },
        }

        cat_objs = {}
        for slug, data in categories.items():
            obj, created = BentoCategory.objects.get_or_create(
                slug=slug, defaults=data
            )
            cat_objs[slug] = obj
            self.stdout.write(
                self.style.SUCCESS(f"🏷️ Created category template: {obj.name}")
                if created
                else self.style.WARNING(f"ℹ️ Category template exists: {obj.name}")
            )

        edge_types = [
            {
                "slug": "supports", "name": "Supports", "rel_type": "SUPPORTS",
                "property_schema": [
                    {"name": "strength", "type": "float", "required": False, "label": "Strength"},
                ],
                "sources": ["idea"], "targets": ["idea"],
            },
            {
                "slug": "about", "name": "About", "rel_type": "ABOUT",
                "property_schema": [],
                "sources": ["idea", "question"], "targets": ["source"],
            },
            {
                "slug": "answered-by", "name": "Answered by", "rel_type": "ANSWERED_BY",
                "property_schema": [],
                "sources": ["question"], "targets": ["idea"],
            },
        ]

        edge_rules = []  # (slug, allowed_source_cats, allowed_target_cats)
        for data in edge_types:
            sources = data.pop("sources", [])
            targets = data.pop("targets", [])
            obj, created = BentoEdgeType.objects.get_or_create(
                slug=data["slug"], defaults=data
            )
            obj.allowed_sources.set([cat_objs[s] for s in sources if s in cat_objs])
            obj.allowed_targets.set([cat_objs[t] for t in targets if t in cat_objs])
            edge_rules.append((data["slug"], sources, targets))
            self.stdout.write(
                self.style.SUCCESS(f"🔗 Created edge-type template: {obj.name}")
                if created
                else self.style.WARNING(f"ℹ️ Edge-type template exists: {obj.name}")
            )

        self.stdout.write(self.style.SUCCESS("✅ Bento template seeding complete."))

        # ── populate the graph with 50 nodes + 30 edges in Neo4j ──────────
        # This is the slow part (≈80 Neo4j round-trips). Thin ingress skips it.
        if self.rich:
            self._seed_graph(edge_rules, n_nodes=50, n_edges=30)
        else:
            self.stdout.write(self.style.WARNING(
                "⏩ Thin ingress (RAVIOLI_RICH_INGRESS=0) — skipping Neo4j graph seeding."
            ))

    # ── minimal graph types: concept / note / references ──────────────────────

    def _seed_graph_types(self):
        """Seed a 'concept' + 'note' node category and a 'references' edge type
        (note → concept). SQL templates only (no Neo4j writes), so it's safe and
        fast in any mode — gated by --seed-graph-types / settings.SEED_GRAPH_TYPES.
        """
        self.stdout.write(self.style.WARNING(
            "🍱 Seeding Bento graph types (concept / note / references)…"
        ))

        categories = {
            "concept": {
                "name": "Concept",
                "neo4j_label": "Concept",
                "description": "A concept or idea that notes can reference.",
                "property_schema": [
                    {"name": "name", "type": "string", "required": True, "label": "Name"},
                    {"name": "body", "type": "text", "required": False, "label": "Body"},
                ],
            },
            "note": {
                "name": "Note",
                "neo4j_label": "Note",
                "description": "A note that may reference one or more concepts.",
                "property_schema": [
                    {"name": "name", "type": "string", "required": True, "label": "Name"},
                    {"name": "body", "type": "text", "required": False, "label": "Body"},
                ],
            },
        }

        cat_objs = {}
        for slug, data in categories.items():
            obj, created = BentoCategory.objects.get_or_create(slug=slug, defaults=data)
            cat_objs[slug] = obj
            self.stdout.write(
                self.style.SUCCESS(f"🏷️ Created category template: {obj.name}")
                if created
                else self.style.WARNING(f"ℹ️ Category template exists: {obj.name}")
            )

        et, created = BentoEdgeType.objects.get_or_create(
            slug="references",
            defaults={"name": "References", "rel_type": "REFERENCES", "property_schema": []},
        )
        et.allowed_sources.set([cat_objs["note"]])
        et.allowed_targets.set([cat_objs["concept"]])
        self.stdout.write(
            self.style.SUCCESS("🔗 Created edge-type template: References (note → concept)")
            if created
            else self.style.WARNING("ℹ️ Edge-type template exists: References (note → concept)")
        )

    # ── templates mirroring the SQL→Neo4j sync (toto.sql_neo4j_sync) ──────────

    @staticmethod
    def _display_name(label):
        return re.sub(r"(?<!^)(?=[A-Z])", " ", label).strip() or label

    @staticmethod
    def _bento_type(model, sql_field, transform):
        if transform in ("json", "default_dict"):
            return "json"
        if transform in ("str", "wkt", "file_url"):
            return "string"
        try:
            internal = model._meta.get_field(sql_field).get_internal_type()
        except Exception:  # noqa: BLE001 — property / reverse / missing field
            return "string"
        return _FIELD_TYPE.get(internal, "string")

    def _schema_from_node(self, node):
        from toto.sql_neo4j_sync.loader import import_model

        try:
            model = import_model(node["model"])
        except Exception:  # noqa: BLE001
            return []
        schema = []
        for neo_name, spec in (node.get("fields") or {}).items():
            if neo_name in RESERVED_PROP_NAMES:
                continue
            if isinstance(spec, dict):
                sql_field, transform = spec.get("source", neo_name), spec.get("transform")
            else:
                sql_field, transform = spec, None
            schema.append({
                "name": neo_name,
                "type": self._bento_type(model, sql_field, transform),
                "required": False,
                "label": neo_name.replace("_", " ").title(),
            })
        return schema

    def _seed_synced_templates(self):
        from toto.sql_neo4j_sync.loader import load_all_configs

        configs = load_all_configs()

        # node categories (one per graph label)
        cat_by_label, new_cats = {}, 0
        for config in configs:
            for node in config.get("nodes", []):
                label = node["label"]
                schema = self._schema_from_node(node)
                cat = BentoCategory.objects.filter(neo4j_label=label).first()
                if cat:
                    cat.property_schema = schema
                    cat.save(update_fields=["property_schema", "updated_at"])
                else:
                    cat = BentoCategory.objects.create(
                        name=self._display_name(label),
                        slug=slugify(label),
                        neo4j_label=label,
                        property_schema=schema,
                        description=f"Synced from {node.get('model', '')}.",
                        # SQL-sync infrastructure types are internal by default — kept
                        # out of the Ingestor's LLM category set (toggle in the UI).
                        internal=True,
                    )
                    new_cats += 1
                cat_by_label[label] = cat

        # edge types (one per relation; allowed source/target categories unioned)
        rels = {}
        for config in configs:
            for link in config.get("links", []):
                relation = link.get("relation")
                if not relation:
                    continue
                entry = rels.setdefault(relation, {"sources": set(), "targets": set()})
                if link.get("from_label"):
                    entry["sources"].add(link["from_label"])
                if not link.get("generic") and link.get("to_label"):
                    entry["targets"].add(link["to_label"])

        new_ets = 0
        for relation, info in rels.items():
            et = BentoEdgeType.objects.filter(rel_type=relation).first()
            if not et:
                et = BentoEdgeType.objects.create(
                    name=relation.replace("_", " ").title(),
                    slug=slugify(relation.replace("_", "-")),
                    rel_type=relation,
                )
                new_ets += 1
            et.allowed_sources.set([cat_by_label[l] for l in info["sources"] if l in cat_by_label])
            et.allowed_targets.set([cat_by_label[l] for l in info["targets"] if l in cat_by_label])

        self.stdout.write(self.style.SUCCESS(
            f"🔗 Synced graph templates: {len(cat_by_label)} categor(ies) "
            f"({new_cats} new), {len(rels)} edge type(s) ({new_ets} new)."
        ))

    def _seed_graph(self, edge_rules, *, n_nodes, n_edges):
        from toto.bento import graph_service as gs
        from toto.ravioli.connection import is_enabled

        if not is_enabled():
            self.stdout.write(self.style.WARNING(
                "ℹ️ Neo4j disabled (RAVIOLI_ENABLED=0) — skipping node/edge seeding."
            ))
            return

        _, existing = gs.list_nodes(limit=1, offset=0)
        if existing >= n_nodes:
            self.stdout.write(self.style.WARNING(
                f"ℹ️ Graph already has {existing} node(s) — skipping node/edge seeding."
            ))
            return

        rng = random.Random(42)
        nodes_by_cat = {"idea": [], "source": [], "question": []}
        topics = ["memory", "storytelling", "compression", "feedback", "incentives",
                  "habits", "attention", "trust", "modularity", "latency", "naming",
                  "caching", "ranking", "evolution", "scarcity"]
        kinds = ["book", "article", "note", "talk", "paper"]

        for i in range(n_nodes):
            cat = rng.choice(["idea", "idea", "source", "question"])  # weight ideas
            topic = rng.choice(topics)
            if cat == "idea":
                props = {"title": f"{topic.title()} idea #{i}",
                         "body": f"A reusable thought about {topic}.",
                         "rating": rng.randint(1, 5)}
            elif cat == "source":
                props = {"title": f"On {topic} (#{i})",
                         "url": f"https://example.org/{topic}/{i}",
                         "kind": rng.choice(kinds)}
            else:
                props = {"title": f"What makes {topic} work? (#{i})",
                         "status": rng.choice(["open", "answered", "parked"])}
            try:
                node = gs.create_node(cat, props)
                nodes_by_cat[cat].append(node["uid"])
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.WARNING(f"⚠️ node create failed: {exc}"))

        total_nodes = sum(len(v) for v in nodes_by_cat.values())
        self.stdout.write(self.style.SUCCESS(f"💡 Created {total_nodes} node(s) in Neo4j."))

        made, attempts = 0, 0
        while made < n_edges and attempts < n_edges * 40:
            attempts += 1
            slug, src_cats, tgt_cats = rng.choice(edge_rules)
            src_pool = [u for c in src_cats for u in nodes_by_cat.get(c, [])]
            tgt_pool = [u for c in tgt_cats for u in nodes_by_cat.get(c, [])]
            if not src_pool or not tgt_pool:
                continue
            a, b = rng.choice(src_pool), rng.choice(tgt_pool)
            if a == b:
                continue
            props = {"strength": round(rng.uniform(0.1, 1.0), 2)} if slug == "supports" else {}
            try:
                gs.create_edge(slug, a, b, props)
                made += 1
            except Exception:  # noqa: BLE001 — duplicate/invalid endpoint, just retry
                continue

        self.stdout.write(self.style.SUCCESS(f"🔗 Created {made} edge(s) in Neo4j."))
