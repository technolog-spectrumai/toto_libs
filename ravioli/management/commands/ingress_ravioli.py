from django.conf import settings
from faker import Faker
from oya.ingress import IngressCommand
from rest_framework.reverse import reverse_lazy

fake = Faker()


class Command(IngressCommand):
    help = "Creates a demo Bolt user with read-only permissions"

    def process(self, _):
        # 📊 Dashboard block
        if settings.DEBUG:
            link = reverse_lazy("ravioli:graph")
        else:
            link = "/neo4j/"

        self.create_dashboard_item(
            title="Graph Database",
            icon="fa-solid fa-database",
            description="Creates one read-only Bolt user for demo purposes.",
            link=link,
            public=False,
        )
