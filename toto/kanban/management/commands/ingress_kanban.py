from datetime import timedelta
import random

from django.contrib.auth.models import User
from django.utils import timezone

from toto.kanban.models import Campaign, Column, DocumentationPage, DocumentationSection, Mission, Project, Sprint, Task
from toto.socialhub.models import Person
from toto.locations.models import Address, Zone, Route
from toto.core.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates a demo kanban setup with multiple campaigns, missions, sprints, tasks, and optional locations"

    def process(self):
        if not self.full:
            return

        # ----------------------------------------------------
        # 👥 Community Members
        # ----------------------------------------------------
        members = list(Person.objects.all())

        if len(members) < 2:
            print("⚠ Skipping Kanban demo: need at least 2 Persons (none found via socialhub ingress).")
            return

        member1, member2 = random.sample(members, 2)
        assignees = [member1, member2]

        # ----------------------------------------------------
        # 🗺️ Optional Locations
        # ----------------------------------------------------
        zones = list(Zone.objects.all())
        addresses = list(Address.objects.all())
        routes = list(Route.objects.all())

        frontend_zone = random.choice(zones) if zones else None
        backend_zone = random.choice(zones) if zones else None

        mission1_location = random.choice(addresses) if addresses else None
        mission2_location = random.choice(addresses) if addresses else None

        mission1_route = random.choice(routes) if routes else None
        mission2_route = random.choice(routes) if routes else None

        if zones:
            print("✔ Using existing zones for demo campaigns.")
        else:
            print("⚠ No zones found. Campaigns will be created without zone.")

        if addresses:
            print("✔ Using existing addresses for demo missions.")
        else:
            print("⚠ No addresses found. Missions will be created without location.")

        if routes:
            print("✔ Using existing routes for demo missions.")
        else:
            print("⚠ No routes found. Missions will be created without route.")

        # ----------------------------------------------------
        # 📁 Project
        # ----------------------------------------------------
        project = Project.objects.create(
            name="Demo Project",
            description="A sample project for Kanban demo",
            owner=member1,
        )

        # ----------------------------------------------------
        # 👤 Add admin user as collaborator
        # ----------------------------------------------------
        try:
            admin_user = User.objects.get(username="admin")
            project.collaborators.add(admin_user)
            print("✔ Added admin user to project collaborators.")
        except User.DoesNotExist:
            print("⚠ No admin user found. Skipping collaborator assignment.")

        # ----------------------------------------------------
        # 📦 Columns
        # ----------------------------------------------------
        todo = Column.objects.create(
            project=project,
            name="To Do",
            position=1,
            can_add_task=True,
        )

        doing = Column.objects.create(
            project=project,
            name="In Progress",
            position=2,
        )

        done = Column.objects.create(
            project=project,
            name="Done",
            position=3,
        )

        all_users = list(User.objects.all())
        auditors = random.sample(all_users, 2) if len(all_users) >= 2 else all_users

        todo.auditors.set(auditors)
        doing.auditors.set(auditors)
        done.auditors.set(auditors)

        # ----------------------------------------------------
        # 📣 Campaigns
        # ----------------------------------------------------
        frontend_campaign = Campaign.objects.create(
            project=project,
            name="Frontend Rollout",
            description="Deliver UI components and wireframes for MVP",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=30)).date(),
            owner=member1,
            zone=frontend_zone,
            metadata={
                "demo": True,
                "focus": "ui",
            },
        )

        backend_campaign = Campaign.objects.create(
            project=project,
            name="Backend API",
            description="Develop core API endpoints and authentication",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=45)).date(),
            owner=member1,
            zone=backend_zone,
            metadata={
                "demo": True,
                "focus": "api",
            },
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
            owner=member1,
            location=mission1_location,
            route=mission1_route,
            metadata={
                "demo": True,
                "release_type": "mvp",
            },
        )

        mission2 = Mission.objects.create(
            campaign=backend_campaign,
            title="Authentication System",
            description="Implement JWT-based authentication and user management",
            urgency=2,
            impact=2,
            owner=member1,
            location=mission2_location,
            route=mission2_route,
            metadata={
                "demo": True,
                "security_area": "authentication",
            },
        )

        # ----------------------------------------------------
        # 📝 Tasks
        # ----------------------------------------------------
        tasks = [
            Task.objects.create(
                column=todo,
                title="Set up project repo",
                position=1,
                mission=mission1,
                assignee=random.choice(assignees),
                weight=3,
                metadata={"demo": True, "kind": "setup"},
            ),
            Task.objects.create(
                column=doing,
                title="Build UI components",
                position=2,
                mission=mission1,
                assignee=random.choice(assignees),
                weight=5,
                metadata={"demo": True, "kind": "frontend"},
            ),
            Task.objects.create(
                column=done,
                title="Create wireframes",
                position=3,
                mission=mission1,
                assignee=random.choice(assignees),
                weight=2,
                metadata={"demo": True, "kind": "design"},
            ),
            Task.objects.create(
                column=todo,
                title="Design database schema",
                position=1,
                mission=mission2,
                assignee=random.choice(assignees),
                weight=3,
                metadata={"demo": True, "kind": "database"},
            ),
            Task.objects.create(
                column=doing,
                title="Implement login endpoint",
                position=2,
                mission=mission2,
                assignee=random.choice(assignees),
                weight=8,
                metadata={"demo": True, "kind": "api"},
            ),
            Task.objects.create(
                column=done,
                title="Unit tests for API",
                position=3,
                mission=mission2,
                assignee=random.choice(assignees),
                weight=1,
                metadata={"demo": True, "kind": "tests"},
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
            end_time=now,
        )

        sprint2 = Sprint.objects.create(
            name="Sprint 2",
            project=project,
            start_time=now,
            end_time=now + timedelta(days=14),
        )

        # Assign tasks to sprints
        for task in tasks[:3]:
            task.sprint = sprint1
            task.save(update_fields=["sprint"])

        for task in tasks[3:]:
            task.sprint = sprint2
            task.save(update_fields=["sprint"])

        # ----------------------------------------------------
        # ✔ Completed tasks
        # ----------------------------------------------------
        tasks[2].completed_at = sprint1.end_time - timedelta(days=2)
        tasks[2].save(update_fields=["completed_at"])

        tasks[5].completed_at = sprint2.start_time + timedelta(days=3)
        tasks[5].save(update_fields=["completed_at"])

        # ----------------------------------------------------
        # 📄 Documentation Pages
        # ----------------------------------------------------
        doc1, _ = DocumentationPage.objects.update_or_create(
            slug="mvp-launch-overview",
            defaults={
                "title": "MVP Launch — Overview",
                "mission": mission1,
                "description": "High-level documentation covering scope, goals, and release criteria for the MVP.",
                "is_manual": False,
            },
        )
        DocumentationSection.objects.get_or_create(
            page=doc1,
            title="Scope",
            defaults={
                "content": "<h2>Scope</h2><p>The MVP covers core authentication, the main dashboard, and basic CRUD operations. Mobile support and advanced analytics are explicitly out of scope for this release.</p>",
                "order": 1,
            },
        )
        DocumentationSection.objects.get_or_create(
            page=doc1,
            title="Release Criteria",
            defaults={
                "content": "<h2>Release Criteria</h2><ul><li>All P0 tasks completed and merged</li><li>End-to-end smoke test passing</li><li>Staging environment signed off by stakeholders</li></ul>",
                "order": 2,
            },
        )

        doc2, _ = DocumentationPage.objects.update_or_create(
            slug="authentication-system-instruction",
            defaults={
                "title": "Authentication System — Instruction",
                "mission": mission2,
                "description": "Step-by-step instruction for setting up JWT-based authentication in the project.",
                "is_manual": True,
            },
        )
        DocumentationSection.objects.get_or_create(
            page=doc2,
            title="Setup",
            defaults={
                "content": "<h2>Setup</h2><p>Install <code>djangorestframework-simplejwt</code> and add it to <code>INSTALLED_APPS</code>. Configure <code>REST_FRAMEWORK</code> to use <code>JWTAuthentication</code> as the default authenticator.</p>",
                "order": 1,
            },
        )
        DocumentationSection.objects.get_or_create(
            page=doc2,
            title="Endpoints",
            defaults={
                "content": "<h2>Endpoints</h2><p>Wire up <code>/api/token/</code> for obtain and <code>/api/token/refresh/</code> for refresh. Protect any view that requires authentication with <code>permission_classes = [IsAuthenticated]</code>.</p>",
                "order": 2,
            },
        )
        DocumentationSection.objects.get_or_create(
            page=doc2,
            title="Testing",
            defaults={
                "content": "<h2>Testing</h2><p>Use the included test client to obtain a token pair and assert that protected endpoints return 401 when the header is absent and 200 when a valid Bearer token is provided.</p>",
                "order": 3,
            },
        )

        print("[Ingress] Demo Kanban setup created successfully.")