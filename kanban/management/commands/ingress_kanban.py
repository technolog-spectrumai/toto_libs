from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from kanban.models import Project, Column, Task, Sprint, Mission, Campaign
from oya.models import Platform, Theme
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates a demo kanban setup with a current sprint using existing ColorMix themes"

    def process(self):
        self.create_dashboard_item(
            title="Tasks",
            icon="fa-solid fa-tasks",
            description="A kanban board with tasks, columns, campaigns, missions, and sprint setup.",
            link="/kanban/",
            public=False,
        )
        if not self.full:
            return

        # Use existing user or create demo
        user, _ = User.objects.get_or_create(username="admin", defaults={"email": "demo@example.com"})

        # Create demo project
        project = Project.objects.create(
            name="Demo Project",
            description="A sample project for Kanban demo",
            owner=user
        )

        # Create demo campaign under project
        campaign = Campaign.objects.create(
            project=project,
            name="Frontend Rollout",
            description="Deliver UI components and wireframes for MVP",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=30)).date(),
            owner=user
        )

        # Create demo mission under campaign
        mission = Mission.objects.create(
            campaign=campaign,
            title="Launch MVP",
            description="Prepare and release the minimum viable product",
            urgency=3,  # High
            impact=3,   # High
            owner=user
        )

        # Create columns directly under project
        todo = Column.objects.create(project=project, name="To Do", position=1)
        doing = Column.objects.create(project=project, name="In Progress", position=2)
        done = Column.objects.create(project=project, name="Done", position=3)

        # Create tasks linked to mission
        task1 = Task.objects.create(column=todo, title="Set up project repo", position=1, mission=mission)
        task2 = Task.objects.create(column=doing, title="Build UI components", position=1, mission=mission)
        task3 = Task.objects.create(column=done, title="Create wireframes", position=1, mission=mission)

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
