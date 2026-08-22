"""The Compute Gears desk: ownership, refusals, and what the page shows."""

from __future__ import annotations

import json
from pathlib import Path

from django.test import override_settings
from django.urls import reverse

from toto.anastasia import choices, services
from toto.anastasia.models import ComputeLease

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
    """A Gear belongs to whoever reserved it. There is no borrowing."""

    def setUp(self):
        super().setUp()
        self.mine = services.reserve(owner=self.user, name="mine", limits=SMALL)
        self.theirs = services.reserve(owner=self.other, name="theirs",
                                       limits=SMALL)

    def test_someone_elses_gear_is_404_not_403(self):
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

    def test_the_index_shows_only_my_gears(self):
        response = self.client.get(reverse("anastasia:index"))
        names = [gear["name"] for gear in response.context["gears"]]
        self.assertEqual(names, ["mine"])

    def test_the_index_is_a_decorated_page_not_a_bare_context(self):
        """The page must go through PageProcessor, and this pins WHY.

        oya/base.html builds its Tailwind palette from `theme.theme.colors` —
        with no `theme` in the context that expression renders `{}`, and every
        custom colour class silently stops existing. This desk shipped exactly
        that way: it ignored the dark-mode toggle, the browser tab read
        "Compute Gears – " with a dangling dash (`platform.site_name` missing),
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

        request = RequestFactory().get("/gears/")
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
    def test_reserving_creates_a_gear(self):
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

    def test_a_nameless_gear_is_refused(self):
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

    def test_setting_a_warm_count_sticks(self):
        self.client.post(reverse("anastasia:set_warm", args=[self.lease.uuid]),
                         {"warm_python": "1"})
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.warm_policy, {"python": 1})

    def test_a_batch_family_is_refused_with_a_reason(self):
        response = self.client.post(
            reverse("anastasia:set_warm", args=[self.lease.uuid]),
            {"warm_pdf": "2"}, follow=True)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("cannot be kept warm" in m for m in messages),
                        messages)


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

    def test_only_mounted_gears_are_polled(self):
        """An unmounted Gear has nothing to report, and asking would wake the
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


class PageTests(DeskTestCase):
    def test_the_page_renders_with_no_gears(self):
        response = self.client.get(reverse("anastasia:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compute Gears")

    def test_the_pool_is_shown_to_everyone_not_just_staff(self):
        """Reserving is a choice made against a number; hiding the number turns
        a refusal into a mystery."""
        response = self.client.get(reverse("anastasia:index"))
        self.assertTrue(response.context["pool"]["configured"])
        self.assertEqual(len(response.context["pool_rows"]), 4)

    def test_a_mounted_gear_renders_its_card(self):
        lease = services.reserve(owner=self.user, name="lab", limits=RUNNABLE)
        services.mount(lease=lease)
        response = self.client.get(reverse("anastasia:index"))
        self.assertContains(response, "lab")

    def test_an_unconfigured_pool_says_so_instead_of_offering_a_form(self):
        with override_settings(ANASTASIA_POOL={}):
            response = self.client.get(reverse("anastasia:index"))
        self.assertFalse(response.context["pool"]["configured"])
        self.assertContains(response, "no compute pool configured")
