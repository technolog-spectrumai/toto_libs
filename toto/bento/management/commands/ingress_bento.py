import random

from toto.ingress import IngressCommand
from toto.bento.models import BentoCategory, BentoEdgeType


class Command(IngressCommand):
    help = "Seed Bento with example node-category and edge-type templates."

    def process(self):
        if not self.full:
            return

        self.stdout.write(self.style.WARNING("🍱 Seeding Bento templates…"))

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
        self._seed_graph(edge_rules, n_nodes=50, n_edges=30)

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
