from django.contrib.auth.models import User
from django.utils import timezone
from oya.ingress import IngressCommand

from ravioli.graph import Node, Edge


class Command(IngressCommand):
    help = "Creates a demo Ravioli graph setup with sample nodes and edges"

    def process(self, _):
        # Add a dashboard item for Ravioli
        self.create_dashboard_item(
            title="Ravioli Graph",
            icon="fa-solid fa-project-diagram",
            description="A demo graph with nodes and edges.",
            link="/ravioli/"
        )

        # if not self.full:
        #     return

        # Ensure demo user exists
        user, _ = User.objects.get_or_create(
            username="admin",
            defaults={"email": "demo@example.com"}
        )

        # Create demo nodes
        alice = Node(
            name="Alice",
            data={"role": "user"},
            metadata={"created_by": user.username, "created_at": str(timezone.now())},
            category="Person"
        ).save()

        bob = Node(
            name="Bob",
            data={"role": "admin"},
            metadata={"created_by": user.username, "created_at": str(timezone.now())},
            category="Person"
        ).save()

        project = Node(
            name="Demo Project",
            data={"description": "A sample project for Ravioli demo"},
            metadata={"created_by": user.username, "created_at": str(timezone.now())},
            category="Project"
        ).save()

        # Create demo edges
        friendship = Edge(
            name="FRIEND",
            metadata={"strength": 0.9, "since": str(timezone.now().date())}
        ).save()

        ownership = Edge(
            name="OWNS",
            metadata={"since": str(timezone.now().date())}
        ).save()

        # Connect Alice -> FRIEND -> Bob
        alice.edges.connect(friendship)
        friendship.target.connect(bob)

        # Connect Bob -> OWNS -> Project
        bob.edges.connect(ownership)
        ownership.target.connect(project)

        self.stdout.write(self.style.SUCCESS("Demo Ravioli graph created successfully."))
