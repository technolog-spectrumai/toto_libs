"""The Compute Capsules desk: ownership, refusals, and what the page shows."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from toto.anastasia import choices, services
from toto.anastasia.models import ComputeLease
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


class FilesDeskTests(DeskTestCase):
    """The Files section: picking files, and the two directions.

    The same acts `test_transfer.py` proves through the bearer API, from the
    page — because they are the same functions (`transfer.to_capsule`,
    `transfer.to_bucket`), and what is under test here is the door: what the
    card offers, what a checked set does, and how a refusal reads.
    """

    def setUp(self):
        super().setUp()
        # Copying OUT writes real vault bytes, and the deployed MEDIA_ROOT is
        # a root-owned bind mount. Each test gets its own directory and takes
        # it away afterwards — test_transfer's fixture, for its reason.
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

    def _url(self, name, lease=None):
        return reverse(f"anastasia:{name}", args=[(lease or self.lease).uuid])

    def _messages(self, response):
        from django.contrib.messages import get_messages

        return [str(m) for m in get_messages(response.wsgi_request)]

    # -- what the card offers ----------------------------------------------

    def test_a_mounted_card_offers_the_files_section(self):
        response = self.client.get(reverse("anastasia:index"))
        self.assertContains(response, "Files")
        self.assertContains(response, self._url("files"))
        self.assertTrue(response.context["files_supported"])

    def test_an_unmounted_capsule_offers_no_files_section(self):
        """The area exists between mounts; READING it needs the executor, and
        a section that could only refuse is worse than no section."""
        services.unmount(lease=self.lease, actor=self.user)
        response = self.client.get(reverse("anastasia:index"))
        self.assertNotContains(response, self._url("files"))

    def test_the_vault_picker_is_drawn_once_for_the_page(self):
        """Not once per Capsule. The tree is every file the person owns, and
        a second Capsule must not double the page."""
        self._vault_file()
        services.mount(lease=services.reserve(owner=self.user, name="two",
                                              limits=SMALL), actor=self.user)
        response = self.client.get(reverse("anastasia:index"))
        body = response.content.decode()
        self.assertEqual(len(response.context["capsules"]), 2)
        self.assertEqual(body.count('name="file"'), 1)

    def test_a_runtime_with_no_files_area_offers_nothing(self):
        class NoFiles(FakeRuntimeBackend):
            capsule_files = None

        with mock.patch("toto.anastasia.views.get_backend",
                        return_value=NoFiles()):
            response = self.client.get(reverse("anastasia:index"))
        self.assertFalse(response.context["files_supported"])

    # -- the listing -------------------------------------------------------

    def test_the_listing_is_owner_only_json(self):
        self._area(**{"out/result.csv": b"a,b\n"})
        body = self.client.get(self._url("files")).json()
        self.assertEqual([f["name"] for f in body["files"]], ["out/result.csv"])
        self.assertTrue(body["complete"])
        self.assertTrue(body["supported"])

    def test_somebody_elses_listing_is_a_404(self):
        theirs = services.reserve(owner=self.other, name="theirs", limits=SMALL)
        self.assertEqual(
            self.client.get(self._url("files", theirs)).status_code, 404)

    def test_an_unanswerable_listing_says_incomplete_rather_than_empty(self):
        """"Nobody answered" and "it is empty" are different claims and only
        one of them is safe to act on."""
        class Silent(FakeRuntimeBackend):
            def capsule_files(self, lease):
                return {}

        with mock.patch("toto.anastasia.views.get_backend",
                        return_value=Silent()):
            body = self.client.get(self._url("files")).json()
        self.assertEqual(body["files"], [])
        self.assertFalse(body["complete"])

    # -- in from the vault -------------------------------------------------

    def test_the_checked_vault_files_are_copied_in(self):
        first, second = self._vault_file(), self._vault_file("notes.txt", b"n")
        response = self.client.post(self._url("file_from_vault"),
                                    {"file": [first.pk, second.pk]})
        self.assertEqual(response.status_code, 302)
        area = FakeRuntimeBackend.files[str(self.lease.uuid)]
        self.assertEqual(area, {"thesis.txt": b"seven years",
                                "notes.txt": b"n"})

    def test_copying_in_is_not_metered(self):
        """Nothing durable is created: the bytes land in capacity the person
        already reserved and already holds, and they die with it."""
        from toto.vault.models import VaultUsageEvent

        vf = self._vault_file()
        before = VaultUsageEvent.objects.count()
        self.client.post(self._url("file_from_vault"), {"file": [vf.pk]})
        self.assertEqual(VaultUsageEvent.objects.count(), before)

    def test_somebody_elses_file_is_refused_by_name_and_the_rest_copied(self):
        mine = self._vault_file()
        theirs = self._vault_file("secret.txt", b"theirs", owner=self.other)
        response = self.client.post(self._url("file_from_vault"),
                                    {"file": [mine.pk, theirs.pk]})
        area = FakeRuntimeBackend.files[str(self.lease.uuid)]
        self.assertEqual(list(area), ["thesis.txt"])
        said = " ".join(self._messages(response))
        self.assertIn("no such file", said)
        # And the refusal never confirms the file exists, only that this
        # person cannot name it.
        self.assertNotIn("secret", said)

    def test_a_name_is_only_honoured_for_a_single_file(self):
        """One name for four files would write four files over each other."""
        one, two = self._vault_file(), self._vault_file("notes.txt", b"n")
        self.client.post(self._url("file_from_vault"),
                         {"file": [one.pk], "name": "in/data.txt"})
        self.assertIn("in/data.txt", FakeRuntimeBackend.files[str(self.lease.uuid)])
        self._area()
        self.client.post(self._url("file_from_vault"),
                         {"file": [one.pk, two.pk], "name": "in/data.txt"})
        self.assertEqual(sorted(FakeRuntimeBackend.files[str(self.lease.uuid)]),
                         ["notes.txt", "thesis.txt"])

    def test_more_than_the_limit_is_bounded_by_the_server(self):
        """The template caps nothing a re-post could not lift; the thing that
        acts is what must refuse."""
        from toto.anastasia import views

        files = [self._vault_file(f"f{i}.txt", b"x") for i in range(6)]
        with mock.patch.object(views, "MAX_FILES_PER_TRANSFER", 3):
            self.client.post(self._url("file_from_vault"),
                             {"file": [f.pk for f in files]})
        self.assertEqual(len(FakeRuntimeBackend.files[str(self.lease.uuid)]), 3)

    def test_picking_nothing_is_a_sentence_not_a_500(self):
        response = self.client.post(self._url("file_from_vault"), {})
        self.assertEqual(response.status_code, 302)
        self.assertIn("at least one", " ".join(self._messages(response)))

    # -- out to a bucket ---------------------------------------------------

    def test_the_checked_capsule_files_become_vault_files(self):
        from toto.vault.models import VaultFile

        self._area(**{"result.csv": b"a,b\n", "log.txt": b"ok\n"})
        response = self.client.post(
            self._url("file_to_vault"),
            {"name": ["result.csv", "log.txt"], "bucket": self.bucket.slug})
        self.assertEqual(response.status_code, 302)
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
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(VaultFile.objects.values_list("title", flat=True)),
                         ["good.txt"])
        said = " ".join(self._messages(response))
        self.assertIn("bad.txt", said)

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
        self.assertEqual(response.status_code, 302)
        self.assertIn("runtime is not answering",
                      " ".join(self._messages(response)))

    # -- deleting ----------------------------------------------------------

    def test_delete_removes_one_file(self):
        self._area(**{"a.txt": b"a", "b.txt": b"b"})
        response = self.client.post(self._url("file_delete"), {"name": "a.txt"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(FakeRuntimeBackend.files[str(self.lease.uuid)]),
                         ["b.txt"])

    def test_deleting_what_is_not_there_is_a_sentence(self):
        self._area()
        response = self.client.post(self._url("file_delete"), {"name": "gone"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("gone", " ".join(self._messages(response)))

    def test_every_verb_refuses_a_get(self):
        for name in ("file_from_vault", "file_to_vault", "file_delete"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self._url(name)).status_code,
                                 405)
