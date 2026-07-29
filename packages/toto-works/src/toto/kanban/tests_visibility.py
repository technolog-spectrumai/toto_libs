"""Mission visibility, zone containment, attachments, and event linking.

The visibility rule: a `project` mission is what every mission was before the
field existed — anyone who reaches the project sees it. A `private` mission is
seen by its owner, the people on visible_to, the project lead, project
auditors, and staff. Everyone else gets querysets that simply do not contain
it, so pages 404 rather than 403 and nothing leaks its existence.
"""

import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.events.models import ScheduledEvent
from toto.kanban.metrics import SprintMetricsCalculator
from toto.kanban.models import (
    Campaign, Mission, MissionAttachment, MissionVisibility, Practitioner,
    Project, ProjectCommitment, Task, TaskStatus, visible_missions_for,
    visible_tasks_for,
)
from toto.people.models import Person

User = get_user_model()

try:
    from toto.locations.models import HAS_GIS
except Exception:  # pragma: no cover
    HAS_GIS = False


def _person(username, **kwargs):
    user = User.objects.create_user(username=username, password="pass", **kwargs)
    person = Person.objects.create(
        user=user, display_name=username.title(), email=f"{username}@x.com"
    )
    return user, person


class VisibilityWorld(TestCase):
    """Six actors around one project holding a public and a private mission."""

    @classmethod
    def setUpTestData(cls):
        cls.lead_user, cls.lead = _person("lead")
        cls.owner_user, cls.owner = _person("owner")
        cls.listed_user, cls.listed = _person("listed")
        cls.auditor_user, cls.auditor_person = _person("auditor")
        cls.member_user, cls.member = _person("member")
        cls.staff_user, cls.staff = _person("staff", is_staff=True)

        cls.project = Project.objects.create(name="P", project_lead=cls.lead)
        auditor_seat = Practitioner.objects.create(person=cls.auditor_person, role="auditor")
        cls.project.auditors.add(auditor_seat)

        member_seat = Practitioner.objects.create(person=cls.member, role="contributor")
        ProjectCommitment.objects.create(
            practitioner=member_seat, project=cls.project, hours_per_day=4
        )

        cls.campaign = Campaign.objects.create(project=cls.project, name="C")
        cls.public_mission = Mission.objects.create(campaign=cls.campaign, title="Public M")
        cls.private_mission = Mission.objects.create(
            campaign=cls.campaign,
            title="Private M",
            visibility=MissionVisibility.PRIVATE,
            owner=cls.owner,
        )
        cls.private_mission.visible_to.add(cls.listed)

        cls.public_task = Task.objects.create(mission=cls.public_mission, title="pub task")
        cls.private_task = Task.objects.create(mission=cls.private_mission, title="priv task")


class VisibleMissionQuerysetTests(VisibilityWorld):
    def seen_by(self, user):
        return set(visible_missions_for(user).values_list("title", flat=True))

    def test_project_visibility_is_the_status_quo(self):
        self.assertIn("Public M", self.seen_by(self.member_user))

    def test_private_hidden_from_a_plain_member(self):
        self.assertNotIn("Private M", self.seen_by(self.member_user))

    def test_owner_sees_their_private_mission(self):
        self.assertIn("Private M", self.seen_by(self.owner_user))

    def test_listed_person_sees_it(self):
        self.assertIn("Private M", self.seen_by(self.listed_user))

    def test_project_lead_sees_it(self):
        self.assertIn("Private M", self.seen_by(self.lead_user))

    def test_auditor_sees_it(self):
        self.assertIn("Private M", self.seen_by(self.auditor_user))

    def test_staff_sees_it(self):
        self.assertIn("Private M", self.seen_by(self.staff_user))

    def test_distinct_holds_when_several_rules_match(self):
        """Owner + listed + auditor at once must still be one row, not three."""
        self.private_mission.visible_to.add(self.owner)
        seat = Practitioner.objects.create(person=self.owner, role="auditor")
        self.project.auditors.add(seat)
        titles = list(
            visible_missions_for(self.owner_user).values_list("title", flat=True)
        )
        self.assertEqual(titles.count("Private M"), 1)

    def test_anonymous_sees_nothing(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(visible_missions_for(AnonymousUser()).count(), 0)

    def test_task_side_join_matches(self):
        titles = set(
            visible_tasks_for(self.member_user).values_list("title", flat=True)
        )
        self.assertIn("pub task", titles)
        self.assertNotIn("priv task", titles)

    def test_user_can_read_agrees_with_the_queryset(self):
        self.assertTrue(self.private_mission.user_can_read(self.owner_user))
        self.assertFalse(self.private_mission.user_can_read(self.member_user))


class PageVisibilityTests(VisibilityWorld):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Platform.objects.create(
            site_name="Test", author="t", publication_year=2026, active=True
        )
        for user in (cls.member_user, cls.owner_user):
            add_to_mesh(user)

    def test_board_hides_the_private_missions_tasks(self):
        self.client.force_login(self.member_user)
        res = self.client.get(f"/kanban/project/{self.project.pk}/")
        body = res.content.decode()
        self.assertIn("pub task", body)
        self.assertNotIn("priv task", body)

    def test_board_shows_them_to_the_owner(self):
        self.client.force_login(self.owner_user)
        body = self.client.get(f"/kanban/project/{self.project.pk}/").content.decode()
        self.assertIn("priv task", body)

    def test_backlog_and_matrix_exclude_the_private_mission(self):
        self.client.force_login(self.member_user)
        for url in (
            f"/kanban/project/{self.project.pk}/backlog/",
            f"/kanban/project/{self.project.pk}/matrix/",
        ):
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                self.assertNotIn("Private M", body)

    def test_mission_page_404s_for_a_member(self):
        self.client.force_login(self.member_user)
        res = self.client.get(f"/kanban/mission/{self.private_mission.pk}/")
        self.assertEqual(res.status_code, 404)

    def test_mission_page_200s_for_the_listed_person(self):
        add_to_mesh(self.listed_user)
        self.client.force_login(self.listed_user)
        res = self.client.get(f"/kanban/mission/{self.private_mission.pk}/")
        self.assertEqual(res.status_code, 200)

    def test_promote_404s_on_an_invisible_task(self):
        self.client.force_login(self.member_user)
        res = self.client.post(
            f"/kanban/{self.project.pk}/task/{self.private_task.pk}/promote/"
        )
        self.assertEqual(res.status_code, 404)


class MetricsVisibilityTests(VisibilityWorld):
    def test_member_totals_exclude_private_tasks(self):
        summary = SprintMetricsCalculator(self.project, user=self.member_user).get_summary()
        self.assertEqual(summary["total_tasks"], 1)

    def test_owner_totals_include_them(self):
        summary = SprintMetricsCalculator(self.project, user=self.owner_user).get_summary()
        self.assertEqual(summary["total_tasks"], 2)

    def test_no_user_means_unfiltered(self):
        summary = SprintMetricsCalculator(self.project).get_summary()
        self.assertEqual(summary["total_tasks"], 2)

    def test_query_count_stays_constant_with_a_user(self):
        with self.assertNumQueries(3):
            SprintMetricsCalculator(self.project, user=self.member_user).get_context_data()


class ApiVisibilityTests(VisibilityWorld):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for user in (cls.member_user, cls.owner_user):
            add_to_mesh(user)

    def test_task_list_excludes_private(self):
        self.client.force_login(self.member_user)
        res = self.client.get(f"/kanban/api/projects/{self.project.pk}/tasks/")
        titles = {t["title"] for t in res.json()["tasks"]}
        self.assertNotIn("priv task", titles)

    def test_mission_detail_404s(self):
        self.client.force_login(self.member_user)
        res = self.client.get(f"/kanban/api/missions/{self.private_mission.pk}/")
        self.assertEqual(res.status_code, 404)

    def test_mission_detail_carries_visibility_for_the_owner(self):
        self.client.force_login(self.owner_user)
        res = self.client.get(f"/kanban/api/missions/{self.private_mission.pk}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["visibility"], "private")

    def test_missions_list_excludes_private(self):
        self.client.force_login(self.member_user)
        res = self.client.get(f"/kanban/api/projects/{self.project.pk}/missions/")
        titles = {m["title"] for m in res.json()["missions"]}
        self.assertNotIn("Private M", titles)

    def test_api_promote_404s_on_an_invisible_task(self):
        self.client.force_login(self.member_user)
        res = self.client.post(f"/kanban/api/tasks/{self.private_task.pk}/promote/")
        self.assertEqual(res.status_code, 404)


class ZoneContainmentTests(TestCase):
    """The refusal the user asked for: a mission zone outside its campaign's
    zone cannot be created (through any form/admin path, via clean)."""

    def setUp(self):
        if not HAS_GIS:
            self.skipTest("geometry fields only exist on a GIS build")

        from django.contrib.gis.geos import MultiPolygon, Polygon
        from toto.locations.models import Zone

        def zone(name, coords):
            return Zone.objects.create(
                name=name,
                geometry=MultiPolygon(Polygon(coords), srid=4326),
            )

        # Outer 0..10, inner 2..4, edge-touching 0..2, outside 20..22.
        self.outer = zone("outer", ((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)))
        self.inner = zone("inner", ((2, 2), (4, 2), (4, 4), (2, 4), (2, 2)))
        self.edge = zone("edge", ((0, 0), (2, 0), (2, 2), (0, 2), (0, 0)))
        self.outside = zone("outside", ((20, 20), (22, 20), (22, 22), (20, 22), (20, 20)))

        _, lead = _person("zonelead")
        project = Project.objects.create(name="ZP", project_lead=lead)
        self.campaign = Campaign.objects.create(project=project, name="ZC", zone=self.outer)

    def _mission(self, zone):
        return Mission(campaign=self.campaign, title="M", zone=zone)

    def test_inside_passes(self):
        self._mission(self.inner).full_clean()

    def test_edge_touching_passes(self):
        """The covers-not-contains regression: a shared border is still inside."""
        self._mission(self.edge).full_clean()

    def test_outside_is_refused_on_the_zone_field(self):
        with self.assertRaises(ValidationError) as ctx:
            self._mission(self.outside).full_clean()
        self.assertIn("zone", ctx.exception.message_dict)

    def test_same_zone_as_campaign_passes(self):
        self._mission(self.outer).full_clean()

    def test_no_campaign_zone_passes(self):
        self.campaign.zone = None
        self.campaign.save()
        self._mission(self.outside).full_clean()

    def test_no_mission_zone_passes(self):
        self._mission(None).full_clean()


# The default MEDIA_ROOT under zenobia/media is root-owned on this machine —
# a known pre-existing condition — so the vault upload goes to a temp dir.
@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="kanban-attach-test-"))
class MissionAttachmentTests(VisibilityWorld):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from toto.vault.models import VaultFile

        cls.vault_file = VaultFile.objects.create(
            owner=cls.owner_user,
            title="spec.pdf",
            file=SimpleUploadedFile("spec.pdf", b"pdf bytes"),
            file_type="pdf",
        )

    def test_unique_per_mission_and_file(self):
        MissionAttachment.objects.create(
            mission=self.public_mission, vault_file=self.vault_file
        )
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MissionAttachment.objects.create(
                    mission=self.public_mission, vault_file=self.vault_file
                )

    def test_add_via_view_is_idempotent(self):
        add_to_mesh(self.owner_user)
        self.client.force_login(self.owner_user)
        # The owner is not a manager here; make them one via commitment.
        seat = Practitioner.objects.create(person=self.owner, role="manager")
        ProjectCommitment.objects.create(
            practitioner=seat, project=self.project, hours_per_day=8
        )
        url = f"/kanban/mission/{self.public_mission.pk}/attachments/add/"
        for _ in range(2):
            self.client.post(url, {"vault_file": self.vault_file.pk})
        self.assertEqual(self.public_mission.attachments.count(), 1)

    def test_outsider_cannot_attach(self):
        outsider_user, _ = _person("outsider")
        self.client.force_login(outsider_user)
        res = self.client.post(
            f"/kanban/mission/{self.public_mission.pk}/attachments/add/",
            {"vault_file": self.vault_file.pk},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.public_mission.attachments.count(), 0)

    def test_inaccessible_file_is_refused(self):
        """A private file belonging to someone else never attaches."""
        self.client.force_login(self.member_user)
        res = self.client.post(
            f"/kanban/mission/{self.public_mission.pk}/attachments/add/",
            {"vault_file": self.vault_file.pk},
            follow=True,
        )
        self.assertEqual(self.public_mission.attachments.count(), 0)

    def test_remove_keeps_the_vault_file(self):
        from toto.vault.models import VaultFile

        attachment = MissionAttachment.objects.create(
            mission=self.public_mission, vault_file=self.vault_file
        )
        self.client.force_login(self.staff_user)
        self.client.post(
            f"/kanban/mission/{self.public_mission.pk}/attachments/{attachment.pk}/remove/"
        )
        self.assertEqual(self.public_mission.attachments.count(), 0)
        self.assertTrue(VaultFile.objects.filter(pk=self.vault_file.pk).exists())


class EventLinkTests(VisibilityWorld):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()
        cls.event = ScheduledEvent.objects.create(
            title="Standup",
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=1),
        )

    def setUp(self):
        self.client.force_login(self.staff_user)

    def test_link_existing_event(self):
        self.client.post(
            f"/kanban/mission/{self.public_mission.pk}/event/link/",
            {"calendar_event": self.event.pk},
        )
        self.public_mission.refresh_from_db()
        self.assertEqual(self.public_mission.calendar_event, self.event)

    def test_unlink_with_an_empty_post(self):
        self.public_mission.calendar_event = self.event
        self.public_mission.save(update_fields=["calendar_event"])
        self.client.post(f"/kanban/mission/{self.public_mission.pk}/event/link/", {})
        self.public_mission.refresh_from_db()
        self.assertIsNone(self.public_mission.calendar_event)

    def test_create_makes_a_private_event_owned_by_the_mission_owner(self):
        self.public_mission.owner = self.owner
        self.public_mission.save(update_fields=["owner"])
        now = timezone.now()
        self.client.post(
            f"/kanban/mission/{self.public_mission.pk}/event/create/",
            {
                "title": "Kickoff",
                "start_time": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "end_time": (now + timedelta(days=2, hours=2)).strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.public_mission.refresh_from_db()
        event = self.public_mission.calendar_event
        self.assertIsNotNone(event)
        self.assertFalse(event.public)
        self.assertEqual(event.owner, self.owner)
        self.assertIn(self.owner, event.organizers.all())

    def test_end_before_start_is_refused(self):
        now = timezone.now()
        self.client.post(
            f"/kanban/mission/{self.public_mission.pk}/event/create/",
            {
                "title": "Broken",
                "start_time": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "end_time": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.public_mission.refresh_from_db()
        self.assertIsNone(self.public_mission.calendar_event)

    def test_deleting_the_event_nulls_the_link(self):
        self.public_mission.calendar_event = self.event
        self.public_mission.save(update_fields=["calendar_event"])
        self.event.delete()
        self.public_mission.refresh_from_db()
        self.assertIsNone(self.public_mission.calendar_event)

    def test_task_event_create_falls_back_to_the_assignee(self):
        seat = Practitioner.objects.create(person=self.owner, role="contributor")
        self.public_task.assignee = seat
        self.public_task.save(update_fields=["assignee"])
        now = timezone.now()
        self.client.post(
            f"/kanban/{self.project.pk}/task/{self.public_task.pk}/event/create/",
            {
                "title": "Do it",
                "start_time": (now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
                "end_time": (now + timedelta(days=3, hours=1)).strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.public_task.refresh_from_db()
        self.assertIsNotNone(self.public_task.calendar_event)
        self.assertEqual(self.public_task.calendar_event.owner, self.owner)


class TaskFormScopingTests(VisibilityWorld):
    def test_mission_dropdown_omits_private_missions_for_a_member(self):
        from toto.kanban.forms import TaskCreateForm

        form = TaskCreateForm(project=self.project, user=self.member_user)
        titles = {m.title for m in form.fields["mission"].queryset}
        self.assertIn("Public M", titles)
        self.assertNotIn("Private M", titles)

    def test_mission_form_refuses_a_foreign_campaign(self):
        from toto.kanban.forms import MissionForm

        _, other_lead = _person("otherlead")
        other_project = Project.objects.create(name="Other", project_lead=other_lead)
        other_campaign = Campaign.objects.create(project=other_project, name="OC")

        form = MissionForm(
            {"title": "X", "campaign": other_campaign.pk, "urgency": 2, "impact": 2,
             "visibility": "project"},
            project=self.project,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("campaign", form.errors)
