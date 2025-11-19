from faker import Faker
from oya.ingress import IngressCommand
from rest_framework.reverse import reverse_lazy

fake = Faker()


class Command(IngressCommand):
    help = "Creates a demo Bolt user with read-only permissions"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Graph Database",
            icon="fa-solid fa-database",
            description="Creates one read-only Bolt user for demo purposes.",
            link=reverse_lazy("ravioli:graph"),
            public=False,
        )