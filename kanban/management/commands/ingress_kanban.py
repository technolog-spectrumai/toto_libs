from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from kanban.models import Project, Column, Task, Sprint, Mission, Campaign
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates a demo kanban setup with multiple campaigns, missions, sprints, and tasks"

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
        other, _ = User.objects.get_or_create(username="developer", defaults={"email": "dev@example.com"})

        # Create demo project
        project = Project.objects.create(
            name="Demo Project",
            description="A sample project for Kanban demo",
            owner=user
        )

        # Columns (no Review column)
        todo = Column.objects.create(project=project, name="To Do", position=1)
        doing = Column.objects.create(project=project, name="In Progress", position=2)
        done = Column.objects.create(project=project, name="Done", position=3)

        # Campaigns
        frontend_campaign = Campaign.objects.create(
            project=project,
            name="Frontend Rollout",
            description="Deliver UI components and wireframes for MVP",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=30)).date(),
            owner=user
        )
        backend_campaign = Campaign.objects.create(
            project=project,
            name="Backend API",
            description="Develop core API endpoints and authentication",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=45)).date(),
            owner=other
        )

        # Missions
        mission1 = Mission.objects.create(
            campaign=frontend_campaign,
            title="Launch MVP",
            description="Prepare and release the minimum viable product",
            urgency=3,
            impact=3,
            owner=user
        )
        mission2 = Mission.objects.create(
            campaign=backend_campaign,
            title="Authentication System",
            description="Implement JWT-based authentication and user management",
            urgency=2,
            impact=3,
            owner=other
        )

        # Tasks (varied columns, missions, assignees, weights)
        tasks = [
            Task.objects.create(column=todo, title="Set up project repo", position=1, mission=mission1, assignee=user, weight=3),
            Task.objects.create(column=doing, title="Build UI components", position=2, mission=mission1, assignee=other, weight=5),
            Task.objects.create(column=done, title="Create wireframes", position=3, mission=mission1, assignee=other, weight=4),
            Task.objects.create(column=todo, title="Design database schema", position=1, mission=mission2, assignee=user, weight=3),
            Task.objects.create(column=doing, title="Implement login endpoint", position=2, mission=mission2, assignee=other, weight=6),
            Task.objects.create(column=done, title="Unit tests for API", position=3, mission=mission2, assignee=other, weight=2),
        ]

        # Sprints
        now = timezone.now()
        sprint1 = Sprint.objects.create(
            name="Sprint 1",
            project=project,
            start_time=now - timedelta(days=14),
            end_time=now
        )
        sprint2 = Sprint.objects.create(
            name="Sprint 2",
            project=project,
            start_time=now,
            end_time=now + timedelta(days=14)
        )

        # Assign tasks to sprints
        for task in tasks[:3]:
            task.sprint = sprint1
            task.save()
        for task in tasks[3:]:
            task.sprint = sprint2
            task.save()

        # Mark some tasks as completed with realistic timestamps
        completed_time1 = sprint1.end_time - timedelta(days=2)
        completed_time2 = sprint2.start_time + timedelta(days=3)

        tasks[2].completed_at = completed_time1  # Create wireframes
        tasks[2].save()

        tasks[5].completed_at = completed_time2  # Unit tests for API
        tasks[5].save()
