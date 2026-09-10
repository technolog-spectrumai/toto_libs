"""The Compute Capsules desk: ownership, refusals, and what the page shows."""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from toto.anastasia import choices, services
from toto.anastasia.models import ComputeLease
from toto.anastasia.samples import CapsuleSample

from .base import RUNNABLE, SMALL, AnastasiaTestCase


#: DIRS beats APP_DIRS, so the stub base wins even on a host that ships the
#: real one — deliberately. These tests are about the desk, not about whatever
#: the platform's chrome happens to look like this week.
_TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [str(Path(__file__).resolve().parent / "templates")],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "django.template.context_processors.request",
    ]},
}]


@override_settings(ROOT_URLCONF="toto.anastasia.tests.urls",
                   TEMPLATES=_TEMPLATES)
class DeskTestCase(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)


class OwnershipTests(DeskTestCase):
    """A Capsule belongs to whoever reserved it. There is no borrowing."""

    def setUp(self):
        super().setUp()
        self.mine = services.reserve(owner=self.user, name="mine", limits=SMALL)
        self.theirs = services.reserve(owner=self.other, name="theirs",
                                       limits=SMALL)

    def test_someone_elses_capsule_is_404_not_403(self):
        """Whether a given uuid exists is not information a stranger needs, and
        a 403 answers exactly that question."""
        for route in ("mount", "unmount", "release"):
            with self.subTest(route=route):
                response = self.client.post(
                    reverse(f"anastasia:{route}", args=[self.theirs.uuid]))
                self.assertEqual(response.status_code, 404)

    def test_someone_elses_status_is_404(self):
        response = self.client.get(
            reverse("anastasia:status", args=[self.theirs.uuid]))
        self.assertEqual(response.status_code, 404)

    def test_the_index_shows_only_my_capsules(self):
        response = self.client.get(reverse("anastasia:index"))
        names = [capsule["name"] for capsule in response.context["capsules"]]
        self.assertEqual(names, ["mine"])

    def test_the_index_is_a_decorated_page_not_a_bare_context(self):
        """The page must go through PageProcessor, and this pins WHY.

        oya/base.html builds its Tailwind palette from `theme.theme.colors` —
        with no `theme` in the context that expression renders `{}`, and every
        custom colour class silently stops existing. This desk shipped exactly
        that way: it ignored the dark-mode toggle, the browser tab read
        "Compute Capsules – " with a dangling dash (`platform.site_name` missing),
        and the Reserve button rendered white-on-nothing — present, clickable,
        invisible. base.html's own comment above the `colors:` line warns that
        one view forgetting PageProcessor costs the page its palette AND its
        dark mode, and every symptom looked like a different bug.
        """
        response = self.client.get(reverse("anastasia:index"))
        for key in ("theme", "platform", "font", "header_nav_items"):
            self.assertIn(key, response.context,
                          f"{key} missing — the view skipped PageProcessor")

    # LOGIN_URL is pinned to a plain path, and ROOT_URLCONF to the package's
    # own: @login_required REVERSES the host's login route, which on a real
    # host is namespaced (sso:login) and is not in this URLconf. Without this
    # the test fails on a namespace lookup that has nothing to do with the
    # thing being tested.
    @override_settings(LOGIN_URL="/login/")
    def test_anonymous_users_are_sent_to_log_in(self):
        """Called directly, through no middleware — the only question here is
        whether @login_required refuses an anonymous caller."""
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from toto.anastasia import views

        request = RequestFactory().get("/capsules/")
        request.user = AnonymousUser()
        response = views.index(request)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("thesis", response.content.decode(errors="replace"))

    def test_a_get_cannot_mount(self):
        """Every state change is a POST; a GET that mounted would be
        reachable from a link in an email."""
        response = self.client.get(
            reverse("anastasia:mount", args=[self.mine.uuid]))
        self.assertEqual(response.status_code, 405)


class ReserveTests(DeskTestCase):
    def test_reserving_creates_a_capsule(self):
        response = self.client.post(reverse("anastasia:reserve"), {
            "name": "thesis", "cpu_millicores": 500, "ram_mb": 1024,
            "scratch_mb": 512, "pids": 128})
        self.assertEqual(response.status_code, 302)
        lease = ComputeLease.objects.get(name="thesis")
        self.assertEqual(lease.owner, self.user)
        self.assertEqual(lease.ram_mb, 1024)

    def test_a_refusal_comes_back_as_a_sentence_not_a_stack_trace(self):
        response = self.client.post(reverse("anastasia:reserve"), {
            "name": "huge", "cpu_millicores": 999999, "ram_mb": 999999,
            "scratch_mb": 999999, "pids": 999999}, follow=True)
        self.assertEqual(response.status_code, 200)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("does not have room" in m for m in messages), messages)
        self.assertFalse(ComputeLease.objects.filter(name="huge").exists())

    def test_junk_numbers_are_refused_without_a_500(self):
        response = self.client.post(reverse("anastasia:reserve"), {
            "name": "junk", "cpu_millicores": "lots", "ram_mb": 1024,
            "scratch_mb": 512, "pids": 128}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ComputeLease.objects.filter(name="junk").exists())

    def test_a_nameless_capsule_is_refused(self):
        response = self.client.post(reverse("anastasia:reserve"), {
            "name": "  ", "cpu_millicores": 500, "ram_mb": 1024,
            "scratch_mb": 512, "pids": 128}, follow=True)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("needs a name" in m for m in messages), messages)


class MountingTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)

    def test_mount_and_unmount_round_trip(self):
        self.client.post(reverse("anastasia:mount", args=[self.lease.uuid]))
        self.assertEqual(services.runtime_for(self.lease).state, choices.READY)
        self.client.post(reverse("anastasia:unmount", args=[self.lease.uuid]))
        self.assertEqual(services.runtime_for(self.lease).state,
                         choices.UNMOUNTED)

    def test_unmounting_keeps_the_reservation(self):
        self.client.post(reverse("anastasia:mount", args=[self.lease.uuid]))
        self.client.post(reverse("anastasia:unmount", args=[self.lease.uuid]))
        self.lease.refresh_from_db()
        self.assertIsNone(self.lease.released_at)

    def test_releasing_returns_the_capacity(self):
        before = services.available()
        self.client.post(reverse("anastasia:release", args=[self.lease.uuid]))
        self.lease.refresh_from_db()
        self.assertIsNotNone(self.lease.released_at)
        self.assertGreater(services.available().ram_mb, before.ram_mb)

    def test_a_mount_that_fails_shows_the_reason(self):
        from .base import FakeRuntimeBackend
        FakeRuntimeBackend.fail_mount = True
        response = self.client.post(
            reverse("anastasia:mount", args=[self.lease.uuid]), follow=True)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("fake manager is down" in m for m in messages),
                        messages)


class WarmPolicyTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)

    # NO WARM TESTS since 2026-09-10. `anastasia:set_warm` was the route and
    # it is deleted with the feature; a test naming it would fail at reverse()
    # rather than assert anything. The desk no longer offers a warm form.


class StatusTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)

    def test_status_is_json_the_card_can_draw(self):
        self.client.post(reverse("anastasia:mount", args=[self.lease.uuid]))
        response = self.client.get(
            reverse("anastasia:status", args=[self.lease.uuid]))
        payload = json.loads(response.content)
        for key in ("uuid", "state", "reserved", "free", "usage",
                    "sample_age_seconds", "executions_running"):
            self.assertIn(key, payload)

    def test_only_mounted_capsules_are_polled(self):
        """An unmounted Capsule has nothing to report, and asking would wake the
        manager every five seconds for nothing."""
        response = self.client.get(reverse("anastasia:index"))
        self.assertEqual(json.loads(response.context["poll_urls_json"]), {})

        self.client.post(reverse("anastasia:mount", args=[self.lease.uuid]))
        response = self.client.get(reverse("anastasia:index"))
        urls = json.loads(response.context["poll_urls_json"])
        self.assertEqual(list(urls), [str(self.lease.uuid)])

    def test_the_pool_endpoint_adds_up(self):
        response = self.client.get(reverse("anastasia:pool"))
        payload = json.loads(response.content)
        for field in ("cpu_millicores", "ram_mb", "scratch_mb", "pids"):
            self.assertEqual(
                payload["booked"][field] + payload["available"][field],
                payload["total"][field])


class SamplesTests(DeskTestCase):
    """The history endpoint behind the card's charts (9.2)."""

    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)
        self.theirs = services.reserve(owner=self.other, name="theirs",
                                       limits=SMALL)
        CapsuleSample.objects.create(lease=self.lease, cpu_percent=10.0,
                                     ram_mb_used=100, storage_bytes=500)

    def _url(self, lease, query=""):
        return reverse("anastasia:samples", args=[lease.uuid]) + query

    def test_the_owner_gets_the_series(self):
        payload = json.loads(self.client.get(self._url(self.lease)).content)
        self.assertEqual(payload["points"], 1)
        cpu = next(m for m in payload["measures"] if m["key"] == "cpu_percent")
        self.assertEqual(cpu["values"], [10.0])

    def test_someone_elses_history_is_404(self):
        """How hard a capsule has been worked is its owner's business, and a
        403 would confirm the uuid exists — the rule every per-capsule route
        in this app follows."""
        response = self.client.get(self._url(self.theirs))
        self.assertEqual(response.status_code, 404)

    def test_a_junk_window_is_a_day_not_a_500(self):
        """The caller is a chart with a query string, not a form with
        validation."""
        payload = json.loads(
            self.client.get(self._url(self.lease, "?hours=lots")).content)
        self.assertEqual(payload["hours"], 24)

    def test_it_carries_counts_and_never_names(self):
        """Storage is a size and a count. A history of CONTENTS would be worse
        than a live listing, because it persists — the boundary
        `executor/storage.py` draws, held at the far end of the wire."""
        payload = json.loads(self.client.get(self._url(self.lease)).content)
        keys = [m["key"] for m in payload["measures"]]
        self.assertNotIn("storage_files", keys)
        for measure in payload["measures"]:
            for value in measure["values"]:
                self.assertNotIsInstance(value, str)


class PageTests(DeskTestCase):
    def test_each_capsule_card_offers_its_history(self):
        """The 9.6 charts. Closed by default and fetched on open, so the page
        carries the series URL and the section, not the data."""
        lease = services.reserve(owner=self.user, name="lab", limits=SMALL)
        response = self.client.get(reverse("anastasia:index"))
        self.assertContains(response, "History")
        self.assertContains(response, reverse("anastasia:samples",
                                              args=[lease.uuid]))
        # A gap is a gap: the one Chart.js option that makes NULL honest is
        # pinned here because it is the easiest thing in the file to lose.
        self.assertContains(response, "spanGaps: false")

    def test_the_page_renders_with_no_capsules(self):
        response = self.client.get(reverse("anastasia:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compute Capsules")

    def test_the_pool_is_shown_to_everyone_not_just_staff(self):
        """Reserving is a choice made against a number; hiding the number turns
        a refusal into a mystery."""
        response = self.client.get(reverse("anastasia:index"))
        self.assertTrue(response.context["pool"]["configured"])
        self.assertEqual(len(response.context["pool_rows"]), 4)

    def test_a_mounted_capsule_renders_its_card(self):
        lease = services.reserve(owner=self.user, name="lab", limits=RUNNABLE)
        services.mount(lease=lease)
        response = self.client.get(reverse("anastasia:index"))
        self.assertContains(response, "lab")

    def test_an_unconfigured_pool_says_so_instead_of_offering_a_form(self):
        with override_settings(ANASTASIA_POOL={}):
            response = self.client.get(reverse("anastasia:index"))
        self.assertFalse(response.context["pool"]["configured"])
        self.assertContains(response, "no compute pool configured")


class OperatorPageTests(DeskTestCase):
    """The staff page: what the machine is doing, and how to stop it.

    Two things under test that the Capsule desk cannot express — that an ordinary
    user cannot reach any of it, and that the two switches are genuinely
    different acts rather than one control with two labels.
    """

    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_user(
            "operator", password="x", is_staff=True)

    def test_an_ordinary_user_cannot_find_it(self):
        """404, not 403. A page whose existence is a 403 tells an unprivileged
        user that there is something there."""
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("anastasia:operator")).status_code, 404)

    def test_an_ordinary_user_cannot_stop_compute(self):
        """The page being hidden is not the control being guarded."""
        self.client.force_login(self.user)
        for action in ("drain", "resume", "stop"):
            with self.subTest(action=action):
                response = self.client.post(
                    reverse("anastasia:operator_control", args=[action]))
                self.assertEqual(response.status_code, 404)

    def test_staff_can_open_it(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("anastasia:operator"))
        self.assertEqual(response.status_code, 200)

    def test_an_unknown_action_is_a_404_not_a_silent_no_op(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("anastasia:operator_control", args=["obliterate"]))
        self.assertEqual(response.status_code, 404)

    def test_the_control_verbs_require_post(self):
        """A GET that drained a host would be reachable from a prefetch."""
        self.client.force_login(self.staff)
        for action in ("drain", "resume", "stop"):
            with self.subTest(action=action):
                response = self.client.get(
                    reverse("anastasia:operator_control", args=[action]))
                self.assertEqual(response.status_code, 405)

    def test_a_control_action_is_audited_with_the_operators_name(self):
        """An emergency stop destroys other people's work. The trail is where
        that decision has to be answerable."""
        from toto.audit.models import AuditRecord

        self.client.force_login(self.staff)
        self.client.post(reverse("anastasia:operator_control", args=["drain"]),
                         {"reason": "kernel upgrade"})
        row = AuditRecord.objects.filter(
            action__iexact="anastasia.control.drain").first()
        self.assertIsNotNone(row)
        self.assertEqual(row.actor_user_id, self.staff.pk)
        self.assertEqual(row.metadata["reason"], "kernel upgrade")

    def test_the_page_renders_with_no_runtime_at_all(self):
        """A staff page that 500s on a host with no executor is a page nobody
        can use to find out why there is no executor."""
        self.client.force_login(self.staff)
        with override_settings(
                ANASTASIA_RUNTIME_BACKEND=
                "toto.anastasia.runtime.NullRuntimeBackend"):
            response = self.client.get(reverse("anastasia:operator"))
        self.assertEqual(response.status_code, 200)


class MovedRouteTests(AnastasiaTestCase):
    """The desk moved from /capsules/ to /capsules/ on 2026-09-10.

    Bookmarks, links in notes and every URL printed in a past support answer
    still say the old one, and a 404 there reads as "the feature was removed"
    rather than "it was renamed".
    """

    def setUp(self):
        super().setUp()
        # The host gates every page behind login, so an anonymous GET is a 302
        # to the login form and never reaches the redirect under test.
        self.client.force_login(self.user)

    def test_the_old_desk_url_redirects_permanently(self):
        response = self.client.get("/gears/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/capsules/")

    def test_a_deep_link_keeps_its_path(self):
        """A per-route list would have to be kept in step with urls.py and
        would not be, so the redirect is a prefix rewrite."""
        response = self.client.get("/gears/operations/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/capsules/operations/")

    def test_the_query_string_survives(self):
        """The reserve form round-trips through one."""
        response = self.client.get("/gears/?name=thesis")
        self.assertEqual(response.status_code, 301)
        self.assertIn("name=thesis", response["Location"])
