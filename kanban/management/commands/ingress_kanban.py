from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from kanban.models import Project, Column, Task, Sprint
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
        user, _ = User.objects.get_or_create(username="admin", defaults={"email": "demo@example.com"})

        # Create demo project
        project = Project.objects.create(
            name="Demo Project",
            description="A sample project for Kanban demo",
            owner=user
        )

        # Create columns directly under project
        todo = Column.objects.create(project=project, name="To Do", position=1)
        doing = Column.objects.create(project=project, name="In Progress", position=2)
        done = Column.objects.create(project=project, name="Done", position=3)

        # Create tasks
        task1 = Task.objects.create(column=todo, title="Set up project repo", position=1)
        task2 = Task.objects.create(column=doing, title="Build UI components", position=1)
        task3 = Task.objects.create(column=done, title="Create wireframes", position=1)

        # Create sprint
        now = timezone.now()
        sprint = Sprint.objects.create(
            name="Sprint 1",
            project=project,
            start_time=now,
            end_time=now + timedelta(days=14)
        )

        # Assign tasks to sprint
        for task in [task1, task2, task3]:
            task.sprint = sprint
            task.save()
