from toto.ingress import IngressCommand
from toto.ravioli.models import CypherQuery, CypherQueryResult


class Command(IngressCommand):
    help = "Seed Ravioli with a single global Cypher query"

    def process(self):

        if not self.full:
            return

        self.stdout.write(self.style.WARNING("🚀 Seeding Ravioli…"))

        # ---------------------------------------------------------
        # 1) Create ONE Cypher Query: "Get Entire Graph"
        # ---------------------------------------------------------
        query_text = """
        MATCH (n)-[r]-(m)
        RETURN n, r, m
        LIMIT 500
        """

        q, created = CypherQuery.objects.get_or_create(
            name="Get Entire Graph",
            defaults={
                "query": query_text.strip(),
                "description": "Returns a large portion of the graph: nodes + relationships.",
            }
        )

        if created:
            self.stdout.write(self.style.SUCCESS("📝 Created Cypher query: Get Entire Graph"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️ Cypher query already exists"))

        # ---------------------------------------------------------
        # 2) Create a CypherQueryResult entry
        # ---------------------------------------------------------
        CypherQueryResult.objects.get_or_create(query=q)

        self.stdout.write(self.style.SUCCESS("📊 Created query result entry"))

        # ---------------------------------------------------------
        # Done
        # ---------------------------------------------------------
        self.stdout.write(self.style.SUCCESS("✅ Ravioli seeding complete."))
