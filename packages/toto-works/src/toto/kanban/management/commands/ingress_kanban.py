from datetime import timedelta
import random

from django.contrib.auth import get_user_model
from django.utils import timezone

from toto.kanban.models import (
    TaskStatus,
    Campaign, DocumentationPage,
    Mission, Practitioner, Project, ProjectCommitment, Sprint, Task,
)
from toto.people.models import Person
from toto.locations.models import Address, Zone, Route
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates a demo kanban setup with practitioners, campaigns, missions, sprints, and tasks"

    def process(self):
        if not self.full:
            return

        members = list(Person.objects.all())
        if len(members) < 2:
            print("⚠ Skipping Kanban demo: need at least 2 Persons.")
            return

        member1, member2 = random.sample(members, 2)

        zones = list(Zone.objects.all())
        addresses = list(Address.objects.all())
        routes = list(Route.objects.all())

        frontend_zone = random.choice(zones) if zones else None
        backend_zone = random.choice(zones) if zones else None
        mission1_location = random.choice(addresses) if addresses else None
        mission2_location = random.choice(addresses) if addresses else None
        mission1_route = random.choice(routes) if routes else None
        mission2_route = random.choice(routes) if routes else None

        # ── Project ───────────────────────────────────────────────────────
        project = Project.objects.create(
            name="Demo Project",
            description="A sample project for Kanban demo",
            project_lead=member1,
        )

        # ── Practitioners ─────────────────────────────────────────────────
        prac1, _ = Practitioner.objects.get_or_create(
            person=member1,
            defaults={"role": Practitioner.ROLE_MANAGER, "is_active": True},
        )
        ProjectCommitment.objects.get_or_create(
            practitioner=prac1, project=project,
            defaults={"hours_per_day": 8, "is_active": True},
        )
        prac2, _ = Practitioner.objects.get_or_create(
            person=member2,
            defaults={"role": Practitioner.ROLE_CONTRIBUTOR, "is_active": True},
        )
        ProjectCommitment.objects.get_or_create(
            practitioner=prac2, project=project,
            defaults={"hours_per_day": 4, "is_active": True},
        )
        practitioners = [prac1, prac2]

        # Ensure the admin/founder also has a Practitioner seat so they can see this board
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True).order_by("id").first()
        if admin_user:
            founder = Person.objects.filter(user=admin_user).first()
            if not founder:
                founder = Person.objects.filter(display_name__icontains="founder").first()
            if founder and founder not in (member1, member2):
                founder_prac, _ = Practitioner.objects.get_or_create(
                    person=founder,
                    defaults={"role": Practitioner.ROLE_MANAGER, "is_active": True},
                )
                ProjectCommitment.objects.get_or_create(
                    practitioner=founder_prac, project=project,
                    defaults={"hours_per_day": 8, "is_active": True},
                )
                practitioners.append(founder_prac)
                print(f"✔  Founder Practitioner: {founder_prac}")

        print(f"✔  Practitioners: {prac1}, {prac2}")

        # ── Who may move cards ────────────────────────────────────────────
        project.auditors.set(practitioners)

        # ── Campaigns ─────────────────────────────────────────────────────
        frontend_campaign = Campaign.objects.create(
            project=project,
            name="Frontend Rollout",
            description="Deliver UI components and wireframes for MVP",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=30)).date(),
            owner=member1,
            zone=frontend_zone,
            metadata={"demo": True, "focus": "ui"},
        )
        backend_campaign = Campaign.objects.create(
            project=project,
            name="Backend API",
            description="Develop core API endpoints and authentication",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=45)).date(),
            owner=member1,
            zone=backend_zone,
            metadata={"demo": True, "focus": "api"},
        )

        # ── Missions ──────────────────────────────────────────────────────
        mission1 = Mission.objects.create(
            campaign=frontend_campaign,
            title="Launch MVP",
            description="Prepare and release the minimum viable product",
            urgency=3, impact=3, owner=member1,
            location=mission1_location, route=mission1_route,
            metadata={"demo": True, "release_type": "mvp"},
        )
        mission2 = Mission.objects.create(
            campaign=backend_campaign,
            title="Authentication System",
            description="Implement JWT-based authentication and user management",
            urgency=2, impact=2, owner=member1,
            location=mission2_location, route=mission2_route,
            metadata={"demo": True, "security_area": "authentication"},
        )

        # ── Tasks ─────────────────────────────────────────────────────────
        tasks = [
            Task.objects.create(status=TaskStatus.TODO,        title="Set up project repo",     position=1, mission=mission1, assignee=random.choice(practitioners), weight=3, metadata={"demo": True}),
            Task.objects.create(status=TaskStatus.IN_PROGRESS, title="Build UI components",     position=2, mission=mission1, assignee=random.choice(practitioners), weight=5, metadata={"demo": True}),
            Task.objects.create(status=TaskStatus.DONE,        title="Create wireframes",       position=3, mission=mission1, assignee=random.choice(practitioners), weight=2, metadata={"demo": True}),
            Task.objects.create(status=TaskStatus.TODO,        title="Design database schema",  position=1, mission=mission2, assignee=random.choice(practitioners), weight=3, metadata={"demo": True}),
            Task.objects.create(status=TaskStatus.IN_PROGRESS, title="Implement login endpoint", position=2, mission=mission2, assignee=random.choice(practitioners), weight=8, metadata={"demo": True}),
            Task.objects.create(status=TaskStatus.DONE,        title="Unit tests for API",      position=3, mission=mission2, assignee=random.choice(practitioners), weight=1, metadata={"demo": True}),
        ]

        # ── Sprints ───────────────────────────────────────────────────────
        now = timezone.now()
        sprint1 = Sprint.objects.create(name="Sprint 1", project=project, start_time=now - timedelta(days=14), end_time=now)
        sprint2 = Sprint.objects.create(name="Sprint 2", project=project, start_time=now, end_time=now + timedelta(days=14))

        for task in tasks[:3]:
            task.sprint = sprint1
            task.save(update_fields=["sprint"])
        for task in tasks[3:]:
            task.sprint = sprint2
            task.save(update_fields=["sprint"])

        # Backdate the two finished tasks so the burndown has something to draw.
        # Task.save only stamps completed_at when it is unset, so assigning an
        # explicit time here survives. Both are in the past — the second used to
        # be three days into the future.
        tasks[2].completed_at = sprint1.end_time - timedelta(days=2)
        tasks[2].save(update_fields=["status", "completed_at"])
        tasks[5].completed_at = now - timedelta(days=1)
        tasks[5].save(update_fields=["status", "completed_at"])

        # ── Wiki ──────────────────────────────────────────────────────────
        # Keyed on (project, slug), not slug alone: the slug is only unique
        # within a project now, so a bare slug lookup can match several rows and
        # update_or_create would raise MultipleObjectsReturned on the second
        # project anyone seeds.
        #
        # Seeds body_html directly. That is the read model every host renders,
        # and on a host with cyprian the first Write picks it up as the seed —
        # so demo data reads correctly whether or not a writer is installed.
        DocumentationPage.objects.update_or_create(
            project=project, slug="mvp-launch-overview",
            defaults={
                "title": "MVP Launch — Overview",
                "mission": mission1,
                "description": "High-level documentation covering scope, goals, and release criteria for the MVP.",
                "is_manual": False,
                "body_html": (
                    "<h2>Scope</h2>"
                    "<p>The MVP covers core authentication, the main dashboard, "
                    "and basic CRUD operations.</p>"
                    "<h2>Release Criteria</h2>"
                    "<ul><li>All P0 tasks completed</li>"
                    "<li>Staging signed off</li></ul>"
                ),
            },
        )

        DocumentationPage.objects.update_or_create(
            project=project, slug="authentication-system-instruction",
            defaults={
                "title": "Authentication System — Instruction",
                "mission": mission2,
                "description": "Step-by-step instruction for setting up JWT-based authentication.",
                "is_manual": True,
                "body_html": (
                    "<h2>Setup</h2>"
                    "<p>Install <code>djangorestframework-simplejwt</code> and "
                    "configure <code>JWTAuthentication</code>.</p>"
                ),
            },
        )

        print("[Ingress] Demo Kanban setup created successfully.")

