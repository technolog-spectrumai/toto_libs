from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone

from toto.ingress import IngressCommand
from toto.kanban.models import Campaign, Column, Mission, Practitioner, Project, ProjectCommitment, Task
from toto.locations.models import Address
from toto.people.models import Person

from ...models import Detection, DetectionCategory
from ...services import first_or_create, skill_metadata


class Command(IngressCommand):
    help = "Seed demo detections, map points, and Kanban mitigation tasks."

    def process(self):
        self.stdout.write("Seeding detections...")
        people = self._ensure_people()
        addresses = self._ensure_addresses()
        categories = self._ensure_categories()
        skills = self._ensure_skills()
        detections = self._ensure_detections(categories, addresses, people)
        tasks = self._ensure_kanban_tasks(detections, people, skills)
        self._seed_workflows()
        self.stdout.write(self.style.SUCCESS(f"Detections ingress complete ({len(tasks)} tasks)."))

    def _seed_workflows(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("seed_detections_workflows", stdout=out, stderr=out)
        for line in out.getvalue().splitlines():
            self.stdout.write(f"  {line}")

    def _point(self, longitude, latitude):
        point = Point(longitude, latitude)
        point.srid = 4326
        return point

    def _ensure_people(self):
        User = get_user_model()
        tester, created = User.objects.get_or_create(
            username="detections_tester",
            defaults={"email": "detections@example.com", "is_staff": True},
        )
        if created:
            tester.set_unusable_password()
            tester.save(update_fields=["password"])
            self.stdout.write("  + user detections_tester")

        specs = [
            ("detections-tester", "Detections Tester", "detections@example.com", tester),
            ("field-operator-ewa", "Ewa Field Operator", "ewa.operator@example.com", None),
            ("response-lead-piotr", "Piotr Response Lead", "piotr.response@example.com", None),
        ]
        people = {}
        for slug, name, email, user in specs:
            person, created = Person.objects.get_or_create(
                slug=slug,
                defaults={"display_name": name, "email": email, "user": user},
            )
            updates = []
            if user and person.user_id != user.pk:
                person.user = user
                updates.append("user")
            if not person.email:
                person.email = email
                updates.append("email")
            if updates:
                person.save(update_fields=updates)
            if created:
                self.stdout.write(f"  + person {name}")
            people[slug.split("-")[0] if slug == "detections-tester" else slug] = person
        people["tester"] = people.pop("detections")
        return people

    def _ensure_addresses(self):
        specs = [
            ("warsaw-north-gate", "Warsaw North Gate", "Warsaw", 21.0156, 52.2477),
            ("krakow-river-yard", "Krakow River Yard", "Krakow", 19.9352, 50.0540),
            ("gdansk-dock-seven", "Gdansk Dock Seven", "Gdansk", 18.6717, 54.4067),
        ]
        addresses = {}
        for key, street, city, longitude, latitude in specs:
            address, created = Address.objects.update_or_create(
                street=street,
                locality_name=city,
                defaults={
                    "country_name": "PL",
                    "state_or_province_name": "",
                    "building": "Field point",
                    "geometry": self._point(longitude, latitude),
                },
            )
            if created:
                self.stdout.write(f"  + address {street}")
            addresses[key] = address
        return addresses

    def _ensure_categories(self):
        specs = [
            ("Access Control", "Gate, lock, badge, and perimeter detections."),
            ("Infrastructure", "Physical infrastructure hazards and damage."),
            ("Safety", "Safety hazards requiring field response."),
        ]
        categories = {}
        for name, description in specs:
            category, created = DetectionCategory.objects.get_or_create(
                name=name,
                defaults={"description": description, "is_active": True},
            )
            if created:
                self.stdout.write(f"  + category {name}")
            categories[name] = category
        return categories

    def _ensure_skills(self):
        if not apps.is_installed("toto.competence"):
            return {}
        SkillGroup = apps.get_model("competence", "SkillGroup")
        SkillBadge = apps.get_model("competence", "SkillBadge")
        group, created = SkillGroup.objects.get_or_create(
            slug="field-response",
            defaults={
                "title": "Field Response",
                "description": "Skills used for detection mitigation and response assignment.",
                "order": 30,
            },
        )
        if created:
            self.stdout.write("  + skill group Field Response")
        specs = [
            ("access-control", "Access Control", "fa-solid fa-id-badge", 10),
            ("site-inspection", "Site Inspection", "fa-solid fa-helmet-safety", 20),
            ("safety-response", "Safety Response", "fa-solid fa-shield-heart", 30),
        ]
        skills = {}
        for slug, title, icon, order in specs:
            badge, created = SkillBadge.objects.get_or_create(
                group=group,
                slug=slug,
                defaults={
                    "title": title,
                    "description": f"{title} skill for detection response.",
                    "icon": icon,
                    "order": order,
                },
            )
            if created:
                self.stdout.write(f"  + skill badge {title}")
            skills[slug] = badge
        return skills

    def _ensure_detections(self, categories, addresses, people):
        now = timezone.now()
        specs = [
            {
                "title": "North gate badge reader offline",
                "description": "Access badge reader is intermittently failing and queueing entry traffic.",
                "category": categories["Access Control"],
                "address": addresses["warsaw-north-gate"],
                "severity": "high",
                "detection_type": "incident",
                "start_time": now - timedelta(hours=3),
            },
            {
                "title": "River yard surface crack",
                "description": "Fresh surface crack near vehicle lane. Needs inspection before heavy traffic resumes.",
                "category": categories["Infrastructure"],
                "address": addresses["krakow-river-yard"],
                "severity": "medium",
                "detection_type": "hazard",
                "start_time": now - timedelta(hours=5),
            },
            {
                "title": "Dock seven loose barrier",
                "description": "Temporary barrier has shifted toward pedestrian flow at the dock approach.",
                "category": categories["Safety"],
                "address": addresses["gdansk-dock-seven"],
                "severity": "low",
                "detection_type": "observation",
                "start_time": now - timedelta(hours=8),
            },
        ]
        detections = []
        for spec in specs:
            detection, created = Detection.objects.update_or_create(
                title=spec["title"],
                defaults={
                    **spec,
                    "status": "new",
                    "reported_by": people["field-operator-ewa"],
                    "public": True,
                },
            )
            if created:
                self.stdout.write(f"  + detection {detection.title}")
            detections.append(detection)
        return detections

    def _ensure_kanban_tasks(self, detections, people, skills):
        owner = people["response-lead-piotr"]
        project, created = first_or_create(
            Project,
            name="Detection Mitigation",
            defaults={
                "description": "Kanban project for detection mitigation and help requests.",
                "project_lead": owner,
            },
        )
        if created:
            self.stdout.write("  + kanban project Detection Mitigation")

        owner_prac, _ = Practitioner.objects.get_or_create(
            person=owner,
            defaults={"role": Practitioner.ROLE_MANAGER, "is_active": True},
        )
        ProjectCommitment.objects.get_or_create(
            practitioner=owner_prac,
            project=project,
            defaults={"hours_per_day": 8, "is_active": True},
        )
        tester_prac, _ = Practitioner.objects.get_or_create(
            person=people["tester"],
            defaults={"role": Practitioner.ROLE_CONTRIBUTOR, "is_active": True},
        )
        ProjectCommitment.objects.get_or_create(
            practitioner=tester_prac,
            project=project,
            defaults={"hours_per_day": 4, "is_active": True},
        )
        auditor_practitioners = [owner_prac, tester_prac]

        columns = {}
        for name, position, can_add in [
            ("To Do", 1, True),
            ("In Progress", 2, False),
            ("Review", 3, False),
            ("Done", 4, False),
        ]:
            column, created = first_or_create(
                Column,
                project=project,
                name=name,
                defaults={"position": position, "can_add_task": can_add},
            )
            column.auditors.set(auditor_practitioners)
            if created:
                self.stdout.write(f"  + kanban column {name}")
            columns[name] = column

        campaign, _ = first_or_create(
            Campaign,
            project=project,
            name="Field Response",
            defaults={
                "description": "Mitigation campaign generated from detections.",
                "start_date": timezone.now().date(),
                "end_date": (timezone.now() + timedelta(days=30)).date(),
                "owner": owner,
                "metadata": {"source": "detections_ingress"},
            },
        )
        mission, _ = first_or_create(
            Mission,
            campaign=campaign,
            title="Mitigate Active Detections",
            defaults={
                "description": "Resolve active detection incidents through normal Kanban task flow.",
                "urgency": 3,
                "impact": 3,
                "owner": owner,
                "metadata": {"source": "detections_ingress"},
            },
        )

        tasks = []
        skill_map = {
            "North gate badge reader offline": ["access-control"],
            "River yard surface crack": ["site-inspection", "safety-response"],
            "Dock seven loose barrier": ["safety-response"],
        }
        for position, detection in enumerate(detections, start=1):
            required_skills = [
                skills[slug]
                for slug in skill_map.get(detection.title, [])
                if slug in skills
            ]
            task, created = first_or_create(
                Task,
                mission=mission,
                title=f"Mitigate: {detection.title}",
                defaults={
                    "description": detection.description,
                    "column": columns["To Do"],
                    "reviewer": owner_prac,
                    "due_date": (timezone.now() + timedelta(days=3)).date(),
                    "position": position,
                    "weight": 3 if detection.severity in ("high", "critical") else 2,
                    "metadata": {
                        "source": "detection",
                        "detection_id": str(detection.pk),
                        "severity": detection.severity,
                        "required_skills": skill_metadata(required_skills),
                    },
                },
            )
            if created:
                self.stdout.write(f"  + kanban task {task.title}")
            elif required_skills:
                metadata = task.metadata or {}
                metadata["required_skills"] = skill_metadata(required_skills)
                task.metadata = metadata
                task.save(update_fields=["metadata"])
            if detection.mitigation_task_id != task.pk:
                detection.mitigation_task = task
                detection.save(update_fields=["mitigation_task", "updated_at"])
            tasks.append(task)
        return tasks
