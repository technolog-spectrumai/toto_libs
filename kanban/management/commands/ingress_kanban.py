from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from kanban.models import Project, Column, Task, Sprint, Mission, Campaign
from community.models import CommunityMember
from oya.ingress import IngressCommand
import random


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

        # ----------------------------------------------------
        # 👥 Get existing community members
        # ----------------------------------------------------
        members = list(CommunityMember.objects.all())

        if len(members) < 2:
            raise Exception("❌ Need at least 2 CommunityMembers to seed Kanban demo.")

        # Pick two for variety
        member1, member2 = random.sample(members, 2)

        # Pool of assignees
        assignees = [member1, member2]

        # ----------------------------------------------------
        # 📁 Create demo project
        # ----------------------------------------------------
        project = Project.objects.create(
            name="Demo Project",
            description="A sample project for Kanban demo",
            owner=member1
        )

        # ----------------------------------------------------
        # 📦 Columns
        # ----------------------------------------------------
        todo = Column.objects.create(project=project, name="To Do", position=1)
        doing = Column.objects.create(project=project, name="In Progress", position=2)
        done = Column.objects.create(project=project, name="Done", position=3)

        # ----------------------------------------------------
        # 📣 Campaigns
        # ----------------------------------------------------
        frontend_campaign = Campaign.objects.create(
            project=project,
            name="Frontend Rollout",
            description="Deliver UI components and wireframes for MVP",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=30)).date(),
            owner=member1
        )

        backend_campaign = Campaign.objects.create(
            project=project,
            name="Backend API",
            description="Develop core API endpoints and authentication",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=45)).date(),
            owner=member1
        )

        # ----------------------------------------------------
        # 🎯 Missions
        # ----------------------------------------------------
        mission1 = Mission.objects.create(
            campaign=frontend_campaign,
            title="Launch MVP",
            description="Prepare and release the minimum viable product",
            urgency=3,
            impact=3,
            owner=member1
        )

        mission2 = Mission.objects.create(
            campaign=backend_campaign,
            title="Authentication System",
            description="Implement JWT-based authentication and user management",
            urgency=2,
            impact=3,
            owner=member1
        )

        # ----------------------------------------------------
        # 📝 Tasks (now using CommunityMember assignees)
        # ----------------------------------------------------
        tasks = [
            Task.objects.create(
                column=todo,
                title="Set up project repo",
                position=1,
                mission=mission1,
                assignee=random.choice(assignees),
                weight=3
            ),
            Task.objects.create(
                column=doing,
                title="Build UI components",
                position=2,
                mission=mission1,
                assignee=random.choice(assignees),
                weight=5
            ),
            Task.objects.create(
                column=done,
                title="Create wireframes",
                position=3,
                mission=mission1,
                assignee=random.choice(assignees),
                weight=4
            ),
            Task.objects.create(
                column=todo,
                title="Design database schema",
                position=1,
                mission=mission2,
                assignee=random.choice(assignees),
                weight=3
            ),
            Task.objects.create(
                column=doing,
                title="Implement login endpoint",
                position=2,
                mission=mission2,
                assignee=random.choice(assignees),
                weight=6
            ),
            Task.objects.create(
                column=done,
                title="Unit tests for API",
                position=3,
                mission=mission2,
                assignee=random.choice(assignees),
                weight=2
            ),
        ]

        # ----------------------------------------------------
        # 🚀 Sprints
        # ----------------------------------------------------
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

        # ----------------------------------------------------
        # ✔ Mark some tasks as completed
        # ----------------------------------------------------
        tasks[2].completed_at = sprint1.end_time - timedelta(days=2)
        tasks[2].save()

        tasks[5].completed_at = sprint2.start_time + timedelta(days=3)
        tasks[5].save()

        print("[Ingress] Demo Kanban setup created successfully.")
