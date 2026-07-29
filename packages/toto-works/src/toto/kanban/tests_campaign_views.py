"""The campaign map and calendar — three levels, filtered by visibility."""

import json
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.events.models import ScheduledEvent
from toto.kanban.models import (
    Campaign, Mission, MissionVisibility, Project, Task, TaskStatus,
)
from toto.locations.models import Address
from toto.people.models import Person

User = get_user_model()

try:
    from toto.locations.models import HAS_GIS
except Exception:  # pragma: no cover
    HAS_GIS = False


class CampaignWorld(TestCase):
    """One zoned campaign; mission A fully geo'd and evented, mission B private."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(
            site_name="Test", author="t", publication_year=2026, active=True
        )
        cls.user = add_to_mesh(User.objects.create_user(username="viewer", password="pass"))
        cls.person = Person.objects.create(
            user=cls.user, display_name="Viewer", email="v@x.com"
        )
        cls.stranger = add_to_mesh(
            User.objects.create_user(username="stranger", password="pass")
        )
        Person.objects.create(
            user=cls.stranger, display_name="Stranger", email="s@x.com"
        )

        cls.project = Project.objects.create(name="P", project_lead=cls.person)

        zone = None
        mission_zone = None
        if HAS_GIS:
            from django.contrib.gis.geos import MultiPolygon, Polygon
            from toto.locations.models import Zone

            zone = Zone.objects.create(
                name="campaign zone",
                geometry=MultiPolygon(
                    Polygon(((0, 0), (10, 0), (10, 10), (0, 10), (0, 0))), srid=4326
                ),
            )
            mission_zone = Zone.objects.create(
                name="mission zone",
                geometry=MultiPolygon(
                    Polygon(((1, 1), (3, 1), (3, 3), (1, 3), (1, 1))), srid=4326
                ),
            )

        cls.campaign = Campaign.objects.create(
            project=cls.project,
            name="C",
            zone=zone,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 21),
        )

        cls.address = Address.objects.create(
            locality_name="Spot", latitude=52.1, longitude=19.2
        )
        event_address = Address.objects.create(
            locality_name="Venue", latitude=52.2, longitude=19.3
        )

        now = timezone.now()
        cls.mission_event = ScheduledEvent.objects.create(
            title="Mission event",
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=2),
            address=event_address,
        )

        cls.mission_a = Mission.objects.create(
            campaign=cls.campaign,
            title="Mission A",
            location=cls.address,
            zone=mission_zone,
            calendar_event=cls.mission_event,
        )
        cls.mission_b = Mission.objects.create(
            campaign=cls.campaign,
            title="Mission B private",
            visibility=MissionVisibility.PRIVATE,
        )

        cls.task_located = Task.objects.create(
            mission=cls.mission_a, title="Located task", location=cls.address
        )
        cls.task_due = Task.objects.create(
            mission=cls.mission_a, title="Due task", due_date=date(2026, 7, 10)
        )
        cls.task_done = Task.objects.create(
            mission=cls.mission_a,
            title="Done task",
            status=TaskStatus.DONE,
            due_date=date(2026, 7, 5),
        )
        cls.task_private = Task.objects.create(
            mission=cls.mission_b, title="Private task", location=cls.address
        )

    def map_data(self, user=None):
        self.client.force_login(user or self.user)
        res = self.client.get(f"/kanban/campaign/{self.campaign.pk}/map/data/")
        self.assertEqual(res.status_code, 200)
        return res.json()

    def kinds(self, data):
        counts = {}
        for feature in data["features"]:
            kind = feature["properties"]["kind"]
            counts[kind] = counts.get(kind, 0) + 1
        return counts


class CampaignMapDataTests(CampaignWorld):
    def test_feature_counts_per_kind(self):
        # The stranger sees only the project-visible mission A, which is what
        # makes the counts deterministic: the viewer is the project lead and
        # would also see the private mission's task.
        counts = self.kinds(self.map_data(self.stranger))
        if HAS_GIS:
            self.assertEqual(counts.get("campaign_zone"), 1)
            self.assertEqual(counts.get("mission_zone"), 1)
        self.assertEqual(counts.get("mission_location"), 1)
        # Mission event + no task events = 1 diamond.
        self.assertEqual(counts.get("event_location"), 1)
        # Mission A's located task alone.
        self.assertEqual(counts.get("task_location"), 1)

    def test_the_lead_also_sees_the_private_missions_task(self):
        counts = self.kinds(self.map_data(self.user))
        self.assertEqual(counts.get("task_location"), 2)

    def test_every_feature_carries_kind_level_label(self):
        for feature in self.map_data()["features"]:
            props = feature["properties"]
            self.assertIn(props["level"], {"campaign", "mission", "task"})
            self.assertTrue(props.get("kind"))
            self.assertTrue(props.get("label"))

    def test_private_mission_and_its_tasks_are_absent(self):
        labels = {
            f["properties"]["label"] for f in self.map_data(self.stranger)["features"]
        }
        self.assertNotIn("Mission B private", labels)
        self.assertNotIn("Private task", labels)

    def test_points_come_from_lat_lon(self):
        for feature in self.map_data()["features"]:
            if feature["properties"]["kind"] == "task_location":
                self.assertEqual(feature["geometry"]["coordinates"], [19.2, 52.1])

    def test_requires_login(self):
        self.client.logout()
        res = self.client.get(f"/kanban/campaign/{self.campaign.pk}/map/data/")
        self.assertEqual(res.status_code, 302)

    def test_gis_off_serves_points_only(self):
        if HAS_GIS:
            self.skipTest("meaningful only on a nogis build")
        counts = self.kinds(self.map_data())
        self.assertNotIn("campaign_zone", counts)
        self.assertNotIn("mission_zone", counts)
        self.assertIn("mission_location", counts)


class CampaignMapPageTests(CampaignWorld):
    def test_renders_with_toggles(self):
        self.client.force_login(self.user)
        res = self.client.get(f"/kanban/campaign/{self.campaign.pk}/map/")
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        for needle in ("campaign-map", "Levels", "campaignMapToggle"):
            self.assertIn(needle, body)

    def test_requires_login(self):
        res = self.client.get(f"/kanban/campaign/{self.campaign.pk}/map/")
        self.assertEqual(res.status_code, 302)

    def test_switcher_lists_sibling_campaigns(self):
        sibling = Campaign.objects.create(project=self.project, name="Sibling C")
        self.client.force_login(self.user)
        body = self.client.get(
            f"/kanban/campaign/{self.campaign.pk}/map/"
        ).content.decode()
        self.assertIn("Sibling C", body)
        self.assertIn(f"/kanban/campaign/{sibling.pk}/map/", body)


class CampaignCalendarTests(CampaignWorld):
    def payload(self, user=None):
        self.client.force_login(user or self.user)
        res = self.client.get(f"/kanban/campaign/{self.campaign.pk}/calendar/")
        self.assertEqual(res.status_code, 200)
        return res, json.loads(res.context["calendar_payload_json"])

    def test_campaign_span_has_the_exclusive_end(self):
        """DateField ranges are inclusive; FullCalendar all-day ends are not."""
        _, payload = self.payload()
        span = payload["campaign"][0]
        self.assertEqual(span["start"], "2026-07-01")
        self.assertEqual(span["end"], "2026-07-22")  # end_date + 1 day
        self.assertTrue(span["allDay"])

    def test_start_only_campaign_has_no_end(self):
        self.campaign.end_date = None
        self.campaign.save(update_fields=["end_date"])
        _, payload = self.payload()
        self.assertNotIn("end", payload["campaign"][0])

    def test_mission_level_carries_only_linked_events(self):
        _, payload = self.payload()
        self.assertEqual(len(payload["mission"]), 1)
        self.assertEqual(payload["mission"][0]["title"], "Mission A")

    def test_task_due_dates_become_all_day_events(self):
        _, payload = self.payload()
        by_title = {e["title"]: e for e in payload["task"]}
        self.assertIn("Due task", by_title)
        self.assertTrue(by_title["Due task"]["allDay"])
        self.assertEqual(by_title["Due task"]["start"], "2026-07-10")

    def test_done_tasks_stay_muted_not_hidden(self):
        _, payload = self.payload()
        by_title = {e["title"]: e for e in payload["task"]}
        self.assertIn("Done task", by_title)
        self.assertNotEqual(
            by_title["Done task"]["color"], by_title["Due task"]["color"]
        )

    def test_unscheduled_tasks_are_absent(self):
        _, payload = self.payload()
        titles = {e["title"] for e in payload["task"]}
        self.assertNotIn("Located task", titles)  # location but no date/event

    def test_private_missions_tasks_are_hidden(self):
        self.task_private.due_date = date(2026, 7, 8)
        self.task_private.save(update_fields=["due_date"])
        _, payload = self.payload(self.stranger)
        titles = {e["title"] for e in payload["task"]}
        self.assertNotIn("Private task", titles)

    def test_initial_date_opens_on_the_campaign(self):
        res, _ = self.payload()
        self.assertEqual(res.context["initial_date"], "2026-07-01")

    def test_level_counts_render_on_the_chips(self):
        res, payload = self.payload()
        self.assertEqual(
            res.context["level_counts"],
            {
                "campaign": len(payload["campaign"]),
                "mission": len(payload["mission"]),
                "task": len(payload["task"]),
            },
        )


class EntryPointTests(CampaignWorld):
    def test_board_links_the_campaign_views(self):
        self.client.force_login(self.user)
        body = self.client.get(f"/kanban/project/{self.project.pk}/").content.decode()
        self.assertIn(f"/kanban/campaign/{self.campaign.pk}/map/", body)
        self.assertIn(f"/kanban/campaign/{self.campaign.pk}/calendar/", body)

    def test_board_renders_with_zero_campaigns(self):
        empty = Project.objects.create(name="Empty", project_lead=self.person)
        self.client.force_login(self.user)
        res = self.client.get(f"/kanban/project/{empty.pk}/")
        self.assertEqual(res.status_code, 200)

    def test_mission_page_links_them_too(self):
        self.client.force_login(self.user)
        body = self.client.get(
            f"/kanban/mission/{self.mission_a.pk}/"
        ).content.decode()
        self.assertIn(f"/kanban/campaign/{self.campaign.pk}/map/", body)
        self.assertIn(f"/kanban/campaign/{self.campaign.pk}/calendar/", body)
