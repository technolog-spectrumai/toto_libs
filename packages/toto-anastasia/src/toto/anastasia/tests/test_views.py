"""The Compute Capsules desk: the list, the Capsule view, and its tabs.

Ownership and refusals first, then what each page shows. The Files tab's
transfers are the same acts `test_transfer.py` proves through the bearer API,
from the page — because they are the same functions — so what is under test
there is the door: what the tab offers, what a checked set does, and how a
refusal reads.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from toto.anastasia import choices, services, views
from toto.anastasia.models import CapsuleEvent, ComputeLease, Execution
from toto.anastasia.samples import CapsuleSample

from .base import RUNNABLE, SMALL, AnastasiaTestCase, FakeRuntimeBackend


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

#: The Capsule view's tabs, by route name and slug, in strip order.
TABS = (("capsule", "information"), ("capsule_history", "history"),
        ("capsule_files", "files"))

X_CLOAK = "[x-cloak]{display:none!important}"


@override_settings(ROOT_URLCONF="toto.anastasia.tests.urls",
                   TEMPLATES=_TEMPLATES)
class DeskTestCase(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _messages(self, response):
        from django.contrib.messages import get_messages

        return [str(m) for m in get_messages(response.wsgi_request)]


# --------------------------------------------------------------------------- #
# Ownership                                                                    #
# --------------------------------------------------------------------------- #

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

    def test_someone_elses_capsule_view_is_404_on_every_tab(self):
        for route, _slug in TABS:
            with self.subTest(route=route):
                response = self.client.get(
                    reverse(f"anastasia:{route}", args=[self.theirs.uuid]))
                self.assertEqual(response.status_code, 404)

    def test_someone_elses_status_and_storage_are_404(self):
        for route in ("status", "storage", "files_list"):
            with self.subTest(route=route):
                response = self.client.get(
                    reverse(f"anastasia:{route}", args=[self.theirs.uuid]))
                self.assertEqual(response.status_code, 404)

    def test_the_index_shows_only_my_capsules(self):
        response = self.client.get(reverse("anastasia:index"))
        names = [capsule["name"] for capsule in response.context["capsules"]]
        self.assertEqual(names, ["mine"])

    def test_every_page_is_decorated_not_a_bare_context(self):
        """Every page must go through PageProcessor, and this pins WHY.

        oya/base.html builds its Tailwind palette from `theme.theme.colors` —
        with no `theme` in the context that expression renders `{}`, and every
        custom colour class silently stops existing. This desk shipped exactly
        that way: it ignored the dark-mode toggle, the browser tab read
        "Compute Capsules – " with a dangling dash, and the Reserve button
        rendered white-on-nothing — present, clickable, invisible.
        """
        urls = [reverse("anastasia:index")] + [
            reverse(f"anastasia:{route}", args=[self.mine.uuid])
            for route, _slug in TABS]
        for url in urls:
            response = self.client.get(url)
            for key in ("theme", "platform", "font", "header_nav_items"):
                with self.subTest(url=url, key=key):
                    self.assertIn(key, response.context,
                                  f"{key} missing — the view skipped PageProcessor")

    # LOGIN_URL is pinned to a plain path, and ROOT_URLCONF to the package's
    # own: @login_required REVERSES the host's login route, which on a real
    # host is namespaced (sso:login) and is not in this URLconf.
    @override_settings(LOGIN_URL="/login/")
    def test_anonymous_users_are_sent_to_log_in(self):
        """Called directly, through no middleware — the only question here is
        whether @login_required refuses an anonymous caller."""
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        for view in (views.index, views.capsule):
            request = RequestFactory().get("/capsules/")
            request.user = AnonymousUser()
            kwargs = {} if view is views.index else {"uuid": self.mine.uuid}
            response = view(request, **kwargs)
            self.assertEqual(response.status_code, 302)
            self.assertNotIn("mine", response.content.decode(errors="replace"))

    def test_a_get_cannot_mount(self):
        """Every state change is a POST; a GET that mounted would be
        reachable from a link in an email."""
        response = self.client.get(
            reverse("anastasia:mount", args=[self.mine.uuid]))
        self.assertEqual(response.status_code, 405)


# --------------------------------------------------------------------------- #
# The list                                                                     #
# --------------------------------------------------------------------------- #

class ListPageTests(DeskTestCase):
    def test_the_page_renders_with_no_capsules(self):
        response = self.client.get(reverse("anastasia:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compute Capsules")
        self.assertContains(response, "You have no Compute Capsules yet.")

    def test_the_pool_is_shown_to_everyone_not_just_staff(self):
        """Reserving is a choice made against a number; hiding the number turns
        a refusal into a mystery."""
        response = self.client.get(reverse("anastasia:index"))
        self.assertTrue(response.context["pool"]["configured"])
        self.assertEqual(len(response.context["pool_rows"]), 4)

    def test_each_card_opens_its_capsule(self):
        lease = services.reserve(owner=self.user, name="lab", limits=SMALL)
        response = self.client.get(reverse("anastasia:index"))
        self.assertContains(response, f'data-capsule-card="{lease.uuid}"')
        self.assertContains(response,
                            reverse("anastasia:capsule", args=[lease.uuid]))

    def test_the_list_draws_neither_history_nor_files(self):
        """What made the old page a wall. Each lives in its tab now, and a
        list that still carried them would fetch them for every card."""
        lease = services.reserve(owner=self.user, name="lab", limits=RUNNABLE)
        services.mount(lease=lease)
        body = self.client.get(reverse("anastasia:index")).content.decode()
        self.assertNotIn(reverse("anastasia:samples", args=[lease.uuid]), body)
        self.assertNotIn(reverse("anastasia:files_list", args=[lease.uuid]), body)
        self.assertNotIn('name="file"', body)
        self.assertNotIn("spanGaps", body)

    def test_reserve_lives_in_a_modal_that_starts_closed(self):
        response = self.client.get(reverse("anastasia:index"))
        self.assertTrue(response.context["can_reserve"])
        self.assertEqual(response.context["open_modal"], "")
        self.assertContains(response, "x-show=\"modal === 'reserve'\"")
        self.assertContains(response, 'data-testid="reserve-button"')

    def test_an_unconfigured_pool_disables_reserve_and_says_why(self):
        with override_settings(ANASTASIA_POOL={}):
            response = self.client.get(reverse("anastasia:index"))
        self.assertFalse(response.context["pool"]["configured"])
        self.assertFalse(response.context["can_reserve"])
        self.assertContains(response, 'data-testid="reserve-why"')
        self.assertContains(response, "no compute pool configured")
        # No modal to open at all: its only outcome would be a refusal.
        self.assertNotContains(response, "x-show=\"modal === 'reserve'\"")

    def test_at_the_limit_reserve_is_disabled_and_says_why(self):
        for index in range(3):
            services.reserve(owner=self.user, name=f"c{index}", limits=SMALL)
        with override_settings(ANASTASIA_MAX_CAPSULES_PER_USER=3):
            response = self.client.get(reverse("anastasia:index"))
        self.assertFalse(response.context["can_reserve"])
        self.assertContains(response, "which is the limit here")

    def test_every_capsule_page_carries_the_x_cloak_rule(self):
        """Without it a modal draws itself until Alpine starts. The desk used
        `x-cloak` for months with no rule behind it."""
        lease = services.reserve(owner=self.user, name="lab", limits=RUNNABLE)
        services.mount(lease=lease)
        urls = [reverse("anastasia:index")] + [
            reverse(f"anastasia:{route}", args=[lease.uuid])
            for route, _slug in TABS]
        for url in urls:
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), X_CLOAK)


class ReserveTests(DeskTestCase):
    def _post(self, **overrides):
        data = {"name": "thesis", "cpu_millicores": 500, "ram_mb": 1024,
                "scratch_mb": 512, "pids": 128, **overrides}
        return self.client.post(reverse("anastasia:reserve"), data)

    def test_reserving_creates_a_capsule_and_opens_it(self):
        response = self._post()
        lease = ComputeLease.objects.get(name="thesis")
        self.assertEqual(lease.owner, self.user)
        self.assertEqual(lease.ram_mb, 1024)
        self.assertRedirects(response,
                             reverse("anastasia:capsule", args=[lease.uuid]),
                             fetch_redirect_response=False)

    def test_a_refusal_reopens_the_modal_holding_what_was_typed(self):
        """The redirect-and-toast this replaced lost five numbers and a name."""
        response = self._post(name="huge", cpu_millicores=999999,
                              ram_mb=999999, scratch_mb=999999, pids=999999)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_modal"], "reserve")
        self.assertIn("does not have room", response.context["reserve_error"])
        self.assertEqual(response.context["reserve"]["name"], "huge")
        self.assertEqual(response.context["reserve"]["cpu_millicores"], "999999")
        self.assertContains(response, 'data-testid="reserve-error"')
        self.assertContains(response, 'value="huge"')
        self.assertFalse(ComputeLease.objects.filter(name="huge").exists())

    def test_junk_numbers_reopen_the_modal_without_a_500(self):
        response = self._post(name="junk", cpu_millicores="lots")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_modal"], "reserve")
        self.assertIn("not whole numbers", response.context["reserve_error"])
        self.assertEqual(response.context["reserve"]["cpu_millicores"], "lots")
        self.assertFalse(ComputeLease.objects.filter(name="junk").exists())

    def test_a_nameless_capsule_is_refused_in_the_modal(self):
        response = self._post(name="  ")
        self.assertEqual(response.context["open_modal"], "reserve")
        self.assertIn("needs a name", response.context["reserve_error"])

    def test_the_internet_tick_survives_a_refusal(self):
        with override_settings(ANASTASIA_EGRESS=True):
            response = self._post(name="huge", ram_mb=999999, egress="on")
        self.assertTrue(response.context["reserve"]["egress"])
        self.assertContains(response, 'name="egress" type="checkbox" class="mt-0.5" checked')

    def test_a_json_caller_still_gets_a_409(self):
        response = self.client.post(
            reverse("anastasia:reserve"),
            {"name": "huge", "cpu_millicores": 999999, "ram_mb": 999999,
             "scratch_mb": 999999, "pids": 999999},
            HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("does not have room", response.json()["error"])


class MountingTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)

    def _verb(self, verb, **data):
        return self.client.post(
            reverse(f"anastasia:{verb}", args=[self.lease.uuid]), data)

    def test_mount_and_unmount_round_trip(self):
        self._verb("mount")
        self.assertEqual(services.runtime_for(self.lease).state, choices.READY)
        self._verb("unmount")
        self.assertEqual(services.runtime_for(self.lease).state,
                         choices.UNMOUNTED)

    def test_a_verb_returns_to_the_tab_it_was_pressed_on(self):
        response = self._verb("mount", tab="files")
        self.assertRedirects(
            response, reverse("anastasia:capsule_files", args=[self.lease.uuid]),
            fetch_redirect_response=False)
        response = self._verb("unmount", tab="history")
        self.assertRedirects(
            response, reverse("anastasia:capsule_history", args=[self.lease.uuid]),
            fetch_redirect_response=False)

    def test_a_forged_tab_can_only_choose_among_real_pages(self):
        response = self._verb("mount", tab="//evil.example/")
        self.assertRedirects(
            response, reverse("anastasia:capsule", args=[self.lease.uuid]),
            fetch_redirect_response=False)

    def test_unmounting_keeps_the_reservation(self):
        self._verb("mount")
        self._verb("unmount")
        self.lease.refresh_from_db()
        self.assertIsNone(self.lease.released_at)

    def test_releasing_returns_the_capacity_and_goes_to_the_list(self):
        before = services.available()
        response = self._verb("release", tab="files")
        self.assertRedirects(response, reverse("anastasia:index"),
                             fetch_redirect_response=False)
        self.lease.refresh_from_db()
        self.assertIsNotNone(self.lease.released_at)
        self.assertGreater(services.available().ram_mb, before.ram_mb)

    def test_a_mount_that_fails_shows_the_reason_on_the_tab(self):
        FakeRuntimeBackend.fail_mount = True
        response = self.client.post(
            reverse("anastasia:mount", args=[self.lease.uuid]),
            {"tab": "history"}, follow=True)
        self.assertEqual(response.context["active_tab"], "history")
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("fake manager is down" in m for m in messages),
                        messages)


class StatusTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)

    def test_status_is_json_the_card_can_draw(self):
        services.mount(lease=self.lease)
        response = self.client.get(
            reverse("anastasia:status", args=[self.lease.uuid]))
        payload = json.loads(response.content)
        for key in ("uuid", "state", "reserved", "free", "usage",
                    "sample_age_seconds", "executions_running"):
            self.assertIn(key, payload)

    def test_only_mounted_capsules_are_polled_from_the_list(self):
        """An unmounted Capsule has nothing to report, and asking would wake the
        manager every five seconds for nothing."""
        response = self.client.get(reverse("anastasia:index"))
        self.assertEqual(json.loads(response.context["poll_urls_json"]), {})

        services.mount(lease=self.lease)
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
    """The history endpoint behind the History tab's charts (9.2)."""

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
        response = self.client.get(self._url(self.theirs))
        self.assertEqual(response.status_code, 404)

    def test_a_junk_window_is_a_day_not_a_500(self):
        payload = json.loads(
            self.client.get(self._url(self.lease, "?hours=lots")).content)
        self.assertEqual(payload["hours"], 24)

    def test_it_carries_counts_and_never_names(self):
        """Storage is a size and a count. A history of CONTENTS would be worse
        than a live listing, because it persists."""
        payload = json.loads(self.client.get(self._url(self.lease)).content)
        keys = [m["key"] for m in payload["measures"]]
        self.assertNotIn("storage_files", keys)
        for measure in payload["measures"]:
            for value in measure["values"]:
                self.assertNotIsInstance(value, str)


# --------------------------------------------------------------------------- #
# The Capsule view                                                             #
# --------------------------------------------------------------------------- #

class CapsuleViewTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)

    def _get(self, route, lease=None):
        return self.client.get(
            reverse(f"anastasia:{route}", args=[(lease or self.lease).uuid]))

    def test_every_tab_renders_for_the_owner_in_one_strip(self):
        for route, slug in TABS:
            with self.subTest(tab=slug):
                response = self._get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["active_tab"], slug)
                self.assertEqual([tab["slug"] for tab in response.context["tabs"]],
                                 [s for _r, s in TABS])
                active = [tab for tab in response.context["tabs"] if tab["active"]]
                self.assertEqual([tab["slug"] for tab in active], [slug])
                self.assertContains(response, f'data-tab="{slug}" aria-current="page"')

    def test_a_released_capsule_has_no_view(self):
        """Not in the list, cannot be mounted, has no files: its tabs would be
        a page of refusals."""
        services.release(lease=self.lease)
        for route, _slug in TABS:
            with self.subTest(route=route):
                self.assertEqual(self._get(route).status_code, 404)

    def test_the_header_offers_the_verb_for_the_state_and_its_tab(self):
        response = self._get("capsule_history")
        self.assertContains(response,
                            reverse("anastasia:mount", args=[self.lease.uuid]))
        self.assertNotContains(response,
                               reverse("anastasia:unmount", args=[self.lease.uuid]))
        self.assertContains(response, '<input type="hidden" name="tab" value="history">')
        self.assertContains(response,
                            reverse("anastasia:release", args=[self.lease.uuid]))

        services.mount(lease=self.lease)
        response = self._get("capsule_history")
        self.assertContains(response,
                            reverse("anastasia:unmount", args=[self.lease.uuid]))
        self.assertNotContains(response,
                               reverse("anastasia:mount", args=[self.lease.uuid]))

    def test_the_header_polls_only_a_mounted_capsule(self):
        self.assertFalse(self._get("capsule").context["mounted"])
        self.assertContains(self._get("capsule"), "poll: false")
        services.mount(lease=self.lease)
        self.assertTrue(self._get("capsule").context["mounted"])
        self.assertContains(self._get("capsule"), "poll: true")

    def test_information_says_when_the_reservation_ends(self):
        """The desk said a reservation lasts N days and never said when THIS
        one ends."""
        response = self._get("capsule")
        self.assertContains(response, 'data-testid="expires-on"')
        self.assertContains(response, 'data-testid="reserved-on"')
        self.assertEqual(response.context["capsule"]["expires_at"],
                         self.lease.expires_at)

    def test_history_carries_the_series_and_the_rule_that_null_is_a_gap(self):
        response = self._get("capsule_history")
        self.assertContains(response,
                            reverse("anastasia:samples", args=[self.lease.uuid]))
        # The one Chart.js option that makes NULL honest, pinned because it is
        # the easiest thing in the file to lose.
        self.assertContains(response, "spanGaps: false")

    def test_history_lists_the_capsules_events_and_jobs(self):
        services.mount(lease=self.lease, actor=self.user)
        Execution.objects.create(
            lease=self.lease, operation="render_pdf", family="pdf",
            cpu_millicores=500, ram_mb=512, scratch_mb=256, pids=64,
            timeout_seconds=60, status=choices.FAILED, exit_code=1,
            error="the renderer said no", requested_by=self.user)
        other = services.reserve(owner=self.user, name="other", limits=SMALL)
        services.mount(lease=other, actor=self.user)

        response = self._get("capsule_history")
        kinds = {row["event"].kind for row in response.context["events"]}
        self.assertIn(CapsuleEvent.MOUNT, kinds)
        self.assertTrue(all(row["event"].lease_id == self.lease.pk
                            for row in response.context["events"]),
                        "another Capsule's events leaked into this history")
        self.assertEqual([row["execution"].operation
                          for row in response.context["jobs"]], ["render_pdf"])
        self.assertContains(response, "the renderer said no")

    def test_storage_says_unsupported_rather_than_zero(self):
        """"We could not measure" and "it is empty" are different claims."""
        class NoStorage(FakeRuntimeBackend):
            storage = None

        with mock.patch("toto.anastasia.views.get_backend",
                        return_value=NoStorage()):
            body = self._get("storage").json()
        self.assertEqual(body, {"supported": False, "complete": False})

        class Measuring(FakeRuntimeBackend):
            def storage(self, lease):
                return {"bytes": 5, "complete": True,
                        "areas": {"files": {"bytes": 5}, "exec": {"bytes": 0}}}

        with mock.patch("toto.anastasia.views.get_backend",
                        return_value=Measuring()):
            body = self._get("storage").json()
        self.assertTrue(body["supported"])
        self.assertEqual(body["areas"]["files"]["bytes"], 5)


# --------------------------------------------------------------------------- #
# Files                                                                        #
# --------------------------------------------------------------------------- #

def _entry(name, size=1, modified=0, is_dir=False):
    return {"name": name, "size": size, "modified": modified, "is_dir": is_dir}


class VaultRowsTests(SimpleTestCase):
    """The executor's flat listing, turned into the Vault's rows."""

    def test_flat_names_become_the_vaults_depth_first_rows(self):
        rows = views._vault_rows([
            _entry("a", 0, is_dir=True), _entry("a/b", 0, is_dir=True),
            _entry("a/b/deep.txt", 3), _entry("a/one.csv", 2),
            _entry("empty", 0, is_dir=True), _entry("root.py", 1),
        ])
        shape = [(r["t"], r.get("name") or r.get("title"), r["depth"])
                 for r in rows]
        # Each folder, then its subfolders, then its own files; root files last.
        self.assertEqual(shape, [
            ("dir", "a", 0), ("dir", "b", 1), ("file", "deep.txt", 2),
            ("file", "one.csv", 1), ("dir", "empty", 0), ("file", "root.py", 0),
        ])
        by = {r.get("path"): r for r in rows}
        self.assertEqual(by["a/b"]["pid"], by["a"]["id"])
        self.assertEqual(by["a/b/deep.txt"]["pid"], by["a/b"]["id"])
        self.assertEqual(by["a/one.csv"]["pid"], by["a"]["id"])
        self.assertIsNone(by["root.py"]["pid"])
        self.assertEqual((by["a"]["n_dirs"], by["a"]["n_files"]), (1, 1))
        self.assertEqual((by["empty"]["n_dirs"], by["empty"]["n_files"]), (0, 0))

    def test_a_file_never_hangs_from_a_parent_that_was_not_listed(self):
        rows = views._vault_rows([_entry("x/y/z.txt")])
        self.assertEqual([(r["t"], r.get("path")) for r in rows],
                         [("dir", "x"), ("dir", "x/y"), ("file", "x/y/z.txt")])

    def test_ids_are_unique_across_folders_and_files(self):
        """A folder and a file sharing an id is how a folder once lit up when a
        file was opened."""
        rows = views._vault_rows([_entry("a", 0, is_dir=True), _entry("a/b"),
                                  _entry("c"), _entry("d/e")])
        ids = [r["id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_type_and_viewability_come_from_the_extension(self):
        rows = {r["title"]: r for r in views._vault_rows([
            _entry("photo.PNG"), _entry("vector.svg"), _entry("page.html"),
            _entry("noext")]) if r["t"] == "file"}
        self.assertEqual((rows["photo.PNG"]["file_type"], rows["photo.PNG"]["ext"]),
                         ("image", "png"))
        self.assertTrue(rows["photo.PNG"]["viewable"])
        # A runner-written SVG or HTML inline on this origin is script.
        self.assertFalse(rows["vector.svg"]["viewable"])
        self.assertFalse(rows["page.html"]["viewable"])
        self.assertEqual((rows["noext"]["file_type"], rows["noext"]["ext"]),
                         ("file", ""))

    def test_modified_is_a_date_and_unknown_is_empty_not_1970(self):
        stamp = 1726000000
        rows = [r for r in views._vault_rows([_entry("a", modified=stamp),
                                              _entry("b", modified=0)])]
        self.assertEqual(rows[0]["modified"],
                         datetime.fromtimestamp(stamp, tz=timezone.utc)
                         .date().isoformat())
        self.assertEqual(rows[1]["modified"], "")

    def test_nothing_is_nothing(self):
        self.assertEqual(views._vault_rows([]), [])
        self.assertEqual(views._vault_rows(None), [])


#: Class strings the Files tab copies from `vault/public_file_list.html`. Each
#: must be in BOTH templates: a Vault-side change is then noticed here rather
#: than the two drifting into two looks.
VAULT_FRAGMENTS = (
    "overflow-hidden rounded-xl border shadow-sm",
    "flex items-center gap-2 cursor-pointer py-3 pr-4 transition-colors duration-150 select-none",
    "group flex items-center gap-3 py-2.5 pr-4 text-sm transition-colors duration-150",
    "padding-left: calc(${item.depth} * 1.5rem + 0.75rem)",
    "w-full rounded-xl border py-2.5 pl-9 pr-9 text-sm focus:outline-none focus:ring-2 focus:ring-current/20 transition",
    "group relative flex flex-col gap-2 rounded-xl border p-3 shadow-sm transition hover:shadow-md",
    "px-3 py-1.5 text-xs font-semibold transition flex items-center gap-1.5",
)


class FilesTabTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        # Copying OUT writes real vault bytes, and the deployed MEDIA_ROOT is
        # a root-owned bind mount. Each test gets its own directory.
        import shutil
        import tempfile

        media = tempfile.mkdtemp(prefix="anastasia-desk-files-")
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)

        from toto.vault.models import Bucket

        self.lease = services.reserve(owner=self.user, name="lab", limits=SMALL)
        services.mount(lease=self.lease, actor=self.user)
        self.bucket = Bucket.objects.create(name="Papers", slug="papers",
                                            owner=self.user,
                                            storage_backend="local")

    # -- fixtures ----------------------------------------------------------

    def _vault_file(self, title="thesis.txt", body=b"seven years", owner=None):
        from django.core.files.base import ContentFile

        from toto.vault.models import VaultFile

        vf = VaultFile(owner=owner or self.user, title=title, bucket=self.bucket,
                       file_type="text", key=title.replace(".", "-"))
        vf.file.save(title, ContentFile(body), save=False)
        vf.save()
        return vf

    def _area(self, **files):
        FakeRuntimeBackend.files[str(self.lease.uuid)] = dict(files)

    def _held(self):
        return FakeRuntimeBackend.files.get(str(self.lease.uuid), {})

    def _url(self, name, lease=None):
        return reverse(f"anastasia:{name}", args=[(lease or self.lease).uuid])

    def _back_to_files(self, response):
        self.assertRedirects(response, self._url("capsule_files"),
                             fetch_redirect_response=False)

    # -- what the tab draws -------------------------------------------------

    def test_the_tab_draws_the_area_as_the_vaults_rows(self):
        self._area(**{"out/result.csv": b"a,b\n", "notes.txt": b"n"})
        response = self.client.get(self._url("capsule_files"))
        self.assertContains(response, 'data-testid="files-browser"')
        rows = response.context["items"]
        self.assertEqual([(r["t"], r.get("path")) for r in rows],
                         [("dir", "out"), ("file", "out/result.csv"),
                          ("file", "notes.txt")])
        self.assertContains(response, 'id="capsule-files-items"')
        self.assertEqual(response.context["folders"], ["out"])

    def test_the_vault_look_is_copied_not_invented(self):
        from django.template import engines

        vault = (engines["django"].get_template("vault/public_file_list.html")
                 .template.source)
        self._area(**{"a/b.txt": b"x"})
        page = self.client.get(self._url("capsule_files")).content.decode()
        for fragment in VAULT_FRAGMENTS:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, vault,
                              "the Vault's markup changed — re-copy it into "
                              "capsule_files.html and update this list")
                self.assertIn(fragment, page)

    def test_the_vault_picker_is_drawn_once(self):
        self._vault_file()
        response = self.client.get(self._url("capsule_files"))
        self.assertEqual(response.content.decode().count('name="file"'), 1)

    def test_an_unmounted_capsule_says_to_mount_and_asks_nobody(self):
        """The area exists between mounts; READING it needs the executor, and
        a tab that could only refuse is worse than one that says what to do."""
        services.unmount(lease=self.lease, actor=self.user)
        FakeRuntimeBackend.calls = []
        response = self.client.get(self._url("capsule_files"))
        self.assertContains(response, 'data-testid="files-unmounted"')
        self.assertNotContains(response, 'data-testid="files-browser"')
        self.assertNotIn("files", [c[0] for c in FakeRuntimeBackend.calls])

    def test_a_runtime_with_no_files_area_says_so(self):
        class NoFiles(FakeRuntimeBackend):
            capsule_files = None

        with mock.patch("toto.anastasia.views.get_backend",
                        return_value=NoFiles()):
            response = self.client.get(self._url("capsule_files"))
        self.assertFalse(response.context["files_supported"])
        self.assertContains(response, 'data-testid="files-unsupported"')

    def test_an_unanswered_listing_says_so_rather_than_empty(self):
        class Silent(FakeRuntimeBackend):
            def capsule_files(self, lease):
                return {}

        with mock.patch("toto.anastasia.views.get_backend",
                        return_value=Silent()):
            response = self.client.get(self._url("capsule_files"))
        self.assertFalse(response.context["answered"])
        self.assertContains(response, 'data-testid="files-unanswered"')

    def test_an_incomplete_listing_says_so(self):
        class Truncated(FakeRuntimeBackend):
            def capsule_files(self, lease):
                return {"files": [_entry("a.txt")], "complete": False}

        with mock.patch("toto.anastasia.views.get_backend",
                        return_value=Truncated()):
            response = self.client.get(self._url("capsule_files"))
        self.assertContains(response, 'data-testid="files-incomplete"')

    def test_the_gauge_measures_against_the_executors_own_budget(self):
        from toto.anastasia.executor import capsules as executor_capsules

        self._area(**{"a.bin": b"x" * 2048})
        response = self.client.get(self._url("capsule_files"))
        self.assertEqual(response.context["held_bytes"], 2048)
        self.assertEqual(response.context["area_budget"],
                         executor_capsules.DEFAULT_AREA_BUDGET)
        self.assertContains(response, 'data-testid="files-gauge"')

    def test_the_json_listing_is_owner_only(self):
        self._area(**{"out/result.csv": b"a,b\n"})
        body = self.client.get(self._url("files_list")).json()
        self.assertEqual([f["name"] for f in body["files"]], ["out/result.csv"])
        self.assertTrue(body["complete"])
        self.assertTrue(body["supported"])

    # -- download -------------------------------------------------------------

    def test_download_returns_the_bytes_as_an_attachment(self):
        self._area(**{"out/report.pdf": b"%PDF-1"})
        response = self.client.get(
            self._url("file_download") + "?name=" + quote("out/report.pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertIn("report.pdf", response["Content-Disposition"])
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_a_name_with_spaces_and_unicode_round_trips(self):
        name = "wyniki/raport końcowy (v2).txt"
        self._area(**{name: b"ok"})
        response = self.client.get(
            self._url("file_download") + "?name=" + quote(name))
        self.assertEqual(b"".join(response.streaming_content), b"ok")

    def test_only_a_raster_image_is_ever_served_inline(self):
        """A runner-written SVG served inline from this origin is script
        running as this site."""
        self._area(**{"pic.png": b"\x89PNG", "vector.svg": b"<svg/>",
                      "page.html": b"<script>"})
        response = self.client.get(self._url("file_download") + "?name=pic.png&view=1")
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        for name in ("vector.svg", "page.html"):
            with self.subTest(name=name):
                response = self.client.get(
                    self._url("file_download") + f"?name={name}&view=1")
                self.assertEqual(response["Content-Type"], "application/octet-stream")
                self.assertTrue(response["Content-Disposition"].startswith("attachment"))

    def test_a_refused_download_is_a_sentence_on_the_files_tab(self):
        self._area()
        response = self.client.get(self._url("file_download") + "?name=gone.txt")
        self._back_to_files(response)
        self.assertIn("gone.txt", " ".join(self._messages(response)))

    def test_a_download_names_a_file_or_is_a_404(self):
        self.assertEqual(self.client.get(self._url("file_download")).status_code, 404)

    def test_somebody_elses_download_is_a_404(self):
        theirs = services.reserve(owner=self.other, name="theirs", limits=SMALL)
        response = self.client.get(
            self._url("file_download", theirs) + "?name=a.txt")
        self.assertEqual(response.status_code, 404)

    def test_download_is_a_get_only(self):
        self.assertEqual(self.client.post(self._url("file_download")).status_code, 405)

    # -- upload ---------------------------------------------------------------

    def _upload(self, *files, **data):
        return self.client.post(self._url("file_upload"),
                                {"files": list(files), **data})

    def test_upload_writes_each_file_into_the_folder(self):
        response = self._upload(SimpleUploadedFile("a.txt", b"hello"),
                                SimpleUploadedFile("b.csv", b"1,2"),
                                folder="/in/data/")
        self._back_to_files(response)
        self.assertEqual(self._held(), {"in/data/a.txt": b"hello",
                                        "in/data/b.csv": b"1,2"})

    def test_upload_to_the_top_of_the_area(self):
        self._upload(SimpleUploadedFile("a.txt", b"hello"))
        self.assertEqual(self._held(), {"a.txt": b"hello"})

    def test_an_oversized_upload_is_refused_by_name_before_it_is_read(self):
        with mock.patch.object(views, "MAX_UPLOAD_BYTES", 3):
            response = self._upload(SimpleUploadedFile("big.bin", b"x" * 10),
                                    SimpleUploadedFile("ok.txt", b"ok"))
        self.assertEqual(self._held(), {"ok.txt": b"ok"})
        said = " ".join(self._messages(response))
        self.assertIn("big.bin", said)
        self.assertIn("upload limit", said)

    def test_the_upload_limit_is_the_apis(self):
        from toto.anastasia import api

        self.assertEqual(views.MAX_UPLOAD_BYTES, api.MAX_TRANSFER_BYTES)

    def test_files_beyond_the_limit_are_refused_and_the_rest_written(self):
        with mock.patch.object(views, "MAX_FILES_PER_TRANSFER", 2):
            response = self._upload(*(SimpleUploadedFile(f"f{i}.txt", b"x")
                                      for i in range(3)))
        self.assertEqual(sorted(self._held()), ["f0.txt", "f1.txt"])
        self.assertIn("more than 2 files", " ".join(self._messages(response)))

    def test_an_upload_the_executor_refuses_is_a_sentence_naming_it(self):
        FakeRuntimeBackend.refuse_files = {"bad.txt"}
        response = self._upload(SimpleUploadedFile("bad.txt", b"x"),
                                SimpleUploadedFile("good.txt", b"y"))
        self.assertEqual(self._held(), {"good.txt": b"y"})
        self.assertIn("bad.txt", " ".join(self._messages(response)))

    def test_a_windows_path_upload_name_becomes_its_leaf(self):
        self._upload(SimpleUploadedFile("C:\\fakepath\\notes.txt", b"n"))
        self.assertEqual(self._held(), {"notes.txt": b"n"})

    def test_uploading_nothing_is_a_sentence(self):
        response = self.client.post(self._url("file_upload"), {})
        self._back_to_files(response)
        self.assertIn("at least one file to upload",
                      " ".join(self._messages(response)))

    # -- in from the vault ----------------------------------------------------

    def test_the_checked_vault_files_are_copied_in(self):
        first, second = self._vault_file(), self._vault_file("notes.txt", b"n")
        response = self.client.post(self._url("file_from_vault"),
                                    {"file": [first.pk, second.pk]})
        self._back_to_files(response)
        self.assertEqual(self._held(), {"thesis.txt": b"seven years",
                                        "notes.txt": b"n"})

    def test_copying_in_to_a_folder_keeps_each_title(self):
        first, second = self._vault_file(), self._vault_file("notes.txt", b"n")
        self.client.post(self._url("file_from_vault"),
                         {"file": [first.pk, second.pk], "folder": "in/"})
        self.assertEqual(sorted(self._held()), ["in/notes.txt", "in/thesis.txt"])

    def test_copying_in_is_not_metered(self):
        """Nothing durable is created: the bytes land in capacity the person
        already reserved and already holds, and they die with it."""
        from toto.vault.models import VaultUsageEvent

        vf = self._vault_file()
        before = VaultUsageEvent.objects.count()
        self.client.post(self._url("file_from_vault"), {"file": [vf.pk]})
        self.assertEqual(VaultUsageEvent.objects.count(), before)

    def test_somebody_elses_file_is_refused_and_the_rest_copied(self):
        mine = self._vault_file()
        theirs = self._vault_file("secret.txt", b"theirs", owner=self.other)
        response = self.client.post(self._url("file_from_vault"),
                                    {"file": [mine.pk, theirs.pk]})
        self.assertEqual(list(self._held()), ["thesis.txt"])
        said = " ".join(self._messages(response))
        self.assertIn("no such file", said)
        # The refusal never confirms the file exists.
        self.assertNotIn("secret", said)

    def test_a_name_is_only_honoured_for_a_single_file(self):
        """One name for four files would write four files over each other."""
        one, two = self._vault_file(), self._vault_file("notes.txt", b"n")
        self.client.post(self._url("file_from_vault"),
                         {"file": [one.pk], "name": "in/data.txt"})
        self.assertIn("in/data.txt", self._held())
        self._area()
        self.client.post(self._url("file_from_vault"),
                         {"file": [one.pk, two.pk], "name": "in/data.txt"})
        self.assertEqual(sorted(self._held()), ["notes.txt", "thesis.txt"])

    def test_more_than_the_limit_is_bounded_by_the_server(self):
        files = [self._vault_file(f"f{i}.txt", b"x") for i in range(6)]
        with mock.patch.object(views, "MAX_FILES_PER_TRANSFER", 3):
            self.client.post(self._url("file_from_vault"),
                             {"file": [f.pk for f in files]})
        self.assertEqual(len(self._held()), 3)

    def test_picking_nothing_is_a_sentence_not_a_500(self):
        response = self.client.post(self._url("file_from_vault"), {})
        self._back_to_files(response)
        self.assertIn("at least one", " ".join(self._messages(response)))

    # -- out to a bucket --------------------------------------------------------

    def test_the_checked_capsule_files_become_vault_files(self):
        from toto.vault.models import VaultFile

        self._area(**{"result.csv": b"a,b\n", "log.txt": b"ok\n"})
        response = self.client.post(
            self._url("file_to_vault"),
            {"name": ["result.csv", "log.txt"], "bucket": self.bucket.slug})
        self._back_to_files(response)
        titles = sorted(VaultFile.objects.filter(owner=self.user)
                        .values_list("title", flat=True))
        self.assertEqual(titles, ["log.txt", "result.csv"])

    def test_copying_out_is_metered_like_an_upload(self):
        """The same function the API calls, so the same price — this is the
        third door onto durable storage and must not be the cheap one."""
        from toto.vault.models import VaultUsageEvent

        self._area(**{"result.csv": b"a,b\n"})
        before = VaultUsageEvent.objects.count()
        self.client.post(self._url("file_to_vault"),
                         {"name": ["result.csv"], "bucket": self.bucket.slug})
        self.assertGreater(VaultUsageEvent.objects.count(), before)

    def test_the_copy_out_modal_says_what_it_costs(self):
        self._area(**{"a.txt": b"a"})
        response = self.client.get(self._url("capsule_files"))
        self.assertContains(response, "charged and virus-scanned")

    def test_a_title_is_only_honoured_for_a_single_file(self):
        from toto.vault.models import VaultFile

        self._area(**{"a.txt": b"a", "b.txt": b"b"})
        self.client.post(self._url("file_to_vault"),
                         {"name": ["a.txt", "b.txt"], "bucket": self.bucket.slug,
                          "title": "One title"})
        self.assertEqual(
            sorted(VaultFile.objects.values_list("title", flat=True)),
            ["a.txt", "b.txt"])

    def test_somebody_elses_bucket_is_a_404(self):
        from toto.vault.models import Bucket

        theirs = Bucket.objects.create(name="Theirs", slug="theirs",
                                       owner=self.other, storage_backend="local")
        self._area(**{"a.txt": b"a"})
        response = self.client.post(self._url("file_to_vault"),
                                    {"name": ["a.txt"], "bucket": theirs.slug})
        self.assertEqual(response.status_code, 404)

    def test_one_refusal_names_that_file_and_the_others_still_copy(self):
        from toto.vault.models import VaultFile

        self._area(**{"good.txt": b"g", "bad.txt": b"b"})
        FakeRuntimeBackend.refuse_files = {"bad.txt"}
        response = self.client.post(
            self._url("file_to_vault"),
            {"name": ["good.txt", "bad.txt"], "bucket": self.bucket.slug})
        self._back_to_files(response)
        self.assertEqual(list(VaultFile.objects.values_list("title", flat=True)),
                         ["good.txt"])
        self.assertIn("bad.txt", " ".join(self._messages(response)))

    def test_an_unreachable_runtime_reads_as_the_runtimes_fault(self):
        """Not the person's mistake, and the sentence must say so."""
        from toto.anastasia.runtime import RuntimeUnavailable

        class Down(FakeRuntimeBackend):
            def capsule_file_read(self, lease, name):
                raise RuntimeUnavailable("the fake manager is down")

        self._area(**{"a.txt": b"a"})
        with mock.patch("toto.anastasia.runtime.get_backend",
                        return_value=Down()):
            response = self.client.post(
                self._url("file_to_vault"),
                {"name": ["a.txt"], "bucket": self.bucket.slug})
        self._back_to_files(response)
        self.assertIn("runtime is not answering",
                      " ".join(self._messages(response)))

    # -- deleting ---------------------------------------------------------------

    def test_delete_removes_one_file(self):
        self._area(**{"a.txt": b"a", "b.txt": b"b"})
        response = self.client.post(self._url("file_delete"), {"name": "a.txt"})
        self._back_to_files(response)
        self.assertEqual(list(self._held()), ["b.txt"])

    def test_deleting_what_is_not_there_is_a_sentence(self):
        self._area()
        response = self.client.post(self._url("file_delete"), {"name": "gone"})
        self._back_to_files(response)
        self.assertIn("gone", " ".join(self._messages(response)))

    def test_every_write_refuses_a_get(self):
        for name in ("file_from_vault", "file_to_vault", "file_delete",
                     "file_upload"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self._url(name)).status_code,
                                 405)


# --------------------------------------------------------------------------- #
# The operator                                                                 #
# --------------------------------------------------------------------------- #

class OperatorPageTests(DeskTestCase):
    """The staff page: what the machine is doing, and how to stop it."""

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
    """The desk moved from /gears/ to /capsules/ on 2026-09-10.

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
        response = self.client.get("/gears/?name=thesis")
        self.assertEqual(response.status_code, 301)
        self.assertIn("name=thesis", response["Location"])
