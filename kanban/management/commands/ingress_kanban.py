from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from kanban.models import Project, Board, Column, Task, Sprint
from oya.models import Platform, Theme
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates a demo kanban setup with a current sprint using existing ColorMix themes"

    def process(self, _):
        self.create_dashboard_item(
            title="Kanban",
            icon="clipboard-list",
            description="A kanban board with tasks, columns, and sprint setup.",
            link="/kanban/"
        )

        # Use existing user or create demo
        user, _ = User.objects.get_or_create(username="demo_user", defaults={"email": "demo@example.com"})

        # Create demo project
        project = Project.objects.create(
            name="Demo Project",
            description="A sample project for Kanban demo",
            owner=user
        )

        # Create board
        board = Board.objects.create(project=project, name="Demo Board")

        # Create columns
        todo = Column.objects.create(board=board, name="To Do", position=1)
        doing = Column.objects.create(board=board, name="In Progress", position=2)
        done = Column.objects.create(board=board, name="Done", position=3)

        # Create tasks
        Task.objects.create(column=todo, title="Set up project repo", position=1)
        Task.objects.create(column=doing, title="Build UI components", position=1)
        Task.objects.create(column=done, title="Create wireframes", position=1)

        # Create sprint
        now = timezone.now()
        sprint = Sprint.objects.create(
            name="Sprint 1",
            project=project,
            start=now,
            end=now + timedelta(days=14)
        )
        sprint.tasks.set(Task.objects.filter(column__board=board))
