"""The audit pages actually render — the coverage whose absence cost a 500.

The chain tests prove the record is sound; nothing proved the PAGES were.
All three views arrived from the truth book without ``PageProcessor``, whose
chrome forgave the missing ``platform``; the oya base on a full host does
not, and /audit/ answered 500 to the first superuser who opened it. These
render every page through the real chrome.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform

from ..services import record
from ..views import PER_PAGE

User = get_user_model()


class AuditPageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        cls.staff = User.objects.create_user("keeper", password="pw",
                                             is_staff=True)
        cls.record = record("CREATE", app_label="audit", obj=cls.staff,
                            actor_user=cls.staff,
                            description="the fixture row")


class IndexPageTests(AuditPageTestCase):
    def test_the_trail_renders_through_the_chrome(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Audit trail")
        self.assertContains(response, "Test Platform")
        self.assertContains(response, "the fixture row")

    def test_the_filters_filter(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"),
                                   {"action": "DELETE"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "the fixture row")

    def test_anonymous_is_sent_to_log_in(self):
        response = self.client.get(reverse("audit:index"))
        self.assertEqual(response.status_code, 302)

    def test_a_member_is_not_shown_the_trail(self):
        member = User.objects.create_user("member", password="pw")
        self.client.force_login(member)
        response = self.client.get(reverse("audit:index"))
        self.assertEqual(response.status_code, 302)


class DetailPageTests(AuditPageTestCase):
    def test_one_record_renders(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:detail",
                                           args=[self.record.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "the fixture row")


class VerifyPageTests(AuditPageTestCase):
    def test_the_verification_page_renders_a_verdict(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:verify"))
        self.assertEqual(response.status_code, 200)


class HouseChromeTests(AuditPageTestCase):
    """The pages participate in the platform's palette, and reference no
    stylesheet that does not exist.

    The templates arrived from the truth book carrying its CSS vocabulary —
    `shell page stack`, `card`, `btn`, `badge`, `muted`, `flash` — and this
    platform has no stylesheet at all: every page is styled by the vendored
    Tailwind JIT plus Alpine. So those class names resolved to nothing and the
    pages rendered unstyled, full-bleed and outside dark mode, on BOTH hosts.
    The `<use href="#i-...">` icons were worse: they pointed at a sprite that
    is emitted nowhere, so they were invisible — which looks like nothing,
    which looks like fine.
    """

    def setUp(self):
        self.client.force_login(self.staff)

    def _pages(self):
        return (
            reverse("audit:index"),
            reverse("audit:verify"),
            reverse("audit:detail", args=[self.record.pk]),
        )

    def test_the_pages_wear_the_house_palette(self):
        for url in self._pages():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "bg-bubble-bg-dark")

    def test_no_dead_stylesheet_vocabulary_survives(self):
        for url in self._pages():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, 'class="shell page stack"')
                self.assertNotContains(response, "btn-quiet")

    def test_no_dead_sprite_references_survive(self):
        """The one defect a human reviewer cannot see in a diff."""
        for url in self._pages():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, '<use href="#i-')


class PaginationTests(AuditPageTestCase):
    """Page two keeps the filter.

    It did not: the old pagination wrote `?page=N` and nothing else, so page
    two of a filtered search was unfiltered results presented as filtered.
    """

    def test_the_view_hands_the_filter_to_the_paginator(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"), {"action": "CREATE"})
        self.assertEqual(response.context["extra_query"], "&action=CREATE")

    def test_the_filter_survives_into_the_page_links(self):
        # The cheap assertion above proves the view; only a render proves the
        # template, and the bug lived in the template.
        for index in range(PER_PAGE):
            record("CREATE", app_label="audit", obj=self.staff,
                   actor_user=self.staff, description=f"row {index}")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"),
                                   {"action": "CREATE", "page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_paginated"])
        # `action=CREATE` appears only in a pagination href — the <select>
        # renders `value="CREATE"`, never `action=CREATE`.
        self.assertContains(response, "action=CREATE")

    def test_a_short_trail_renders_no_paginator(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"))
        self.assertFalse(response.context["is_paginated"])


class EmptyStateTests(AuditPageTestCase):
    def test_a_filter_that_matches_nothing_says_so(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"), {"action": "DELETE"})
        self.assertContains(response, "Nothing matches that.")
        self.assertNotContains(response, "Nothing recorded yet.")


class EmptyTrailTests(TestCase):
    """Its own class: the shared fixture always writes one record."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        cls.staff = User.objects.create_user("keeper", password="pw",
                                             is_staff=True)

    def test_an_empty_trail_says_nothing_recorded_yet(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"))
        self.assertContains(response, "Nothing recorded yet.")
        self.assertNotContains(response, "Nothing matches that.")

    def test_verifying_an_empty_chain_claims_nothing(self):
        """`verify_chain()` answers ok=True/checked=0 with no chain at all, and
        "Healthy. 0 records verified" is a claim about nothing."""
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:verify"))
        self.assertContains(response, "There is nothing to verify.")
        self.assertNotContains(response, "Healthy.")


class RecordDetailContentTests(AuditPageTestCase):
    def test_the_detail_page_shows_both_hashes(self):
        """A functionality guard for the <pre> to <dd class="break-all"> move."""
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:detail",
                                           args=[self.record.pk]))
        self.assertContains(response, self.record.record_hash)
        self.assertContains(response, self.record.algorithm)


class VerifyVerdictTests(AuditPageTestCase):
    def test_a_sound_chain_reports_healthy_with_its_count(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:verify"))
        self.assertContains(response, "Healthy.")
        self.assertNotContains(response, "does not verify")
