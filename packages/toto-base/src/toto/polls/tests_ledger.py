"""The ledger: scope-locked, filterable, and it materializes what it shows."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Choice, Decision, Kind, Outcome, Question
from .views import LEDGER_PAGE_SIZE

User = get_user_model()

def _platform():
    from toto.core.models import Platform

    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})



def _decided(title, *, outcome=Outcome.NO_BALLOTS, scope_type="", scope_id="",
             decided_at=None):
    """A decision row without the whole voting ceremony — the ledger reads
    columns, so columns are what the fixture writes."""
    question = Question.objects.create(
        title=title, question_text="Well?", kind=Kind.VOTE,
        scope_type=scope_type, scope_id=scope_id)
    decision = Decision.objects.create(
        question=question, scope_type=scope_type, scope_id=scope_id,
        title=title, outcome=outcome, content={})
    if decided_at is not None:
        Decision.objects.filter(pk=decision.pk).update(decided_at=decided_at)
        decision.refresh_from_db()
    return decision


class LedgerScopeTests(TestCase):
    def setUp(self):
        _platform()
        self.client.force_login(User.objects.create_user("u", password="pw"))

    def test_a_company_decision_never_leaks_into_the_global_ledger(self):
        _decided("Global thing")
        _decided("Community secret", scope_type="socialhub.community",
                 scope_id="1")

        response = self.client.get(reverse("polls:decision_ledger"))

        self.assertContains(response, "Global thing")
        self.assertNotContains(response, "Community secret")


class LedgerFilterTests(TestCase):
    def setUp(self):
        _platform()
        self.client.force_login(User.objects.create_user("u", password="pw"))

    def test_the_outcome_filter(self):
        _decided("Won", outcome=Outcome.WINNER)
        _decided("Tied", outcome=Outcome.TIE)

        response = self.client.get(reverse("polls:decision_ledger"),
                                   {"outcome": "tie"})

        self.assertContains(response, "Tied")
        self.assertNotContains(response, "Won")

    def test_the_date_range_filter(self):
        old = timezone.now() - timedelta(days=30)
        _decided("Ancient", decided_at=old)
        _decided("Fresh")

        day = (timezone.now() - timedelta(days=7)).date().isoformat()
        response = self.client.get(reverse("polls:decision_ledger"),
                                   {"from": day})

        self.assertContains(response, "Fresh")
        self.assertNotContains(response, "Ancient")

    def test_a_malformed_date_is_ignored_not_an_error_page(self):
        _decided("Fresh")

        response = self.client.get(reverse("polls:decision_ledger"),
                                   {"from": "not-a-date"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fresh")

    def test_pagination(self):
        for i in range(LEDGER_PAGE_SIZE + 1):
            _decided(f"Decision {i}")

        first = self.client.get(reverse("polls:decision_ledger"))
        second = self.client.get(reverse("polls:decision_ledger"),
                                 {"page": 2})

        self.assertContains(first, "Page 1 of 2")
        self.assertEqual(second.status_code, 200)
        # Newest first: the oldest row is the one that falls to page 2.
        self.assertContains(second, "Decision 0")


class LedgerViewTests(TestCase):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("polls:decision_ledger"))

        self.assertEqual(response.status_code, 302)

    def test_visiting_materializes_overdue_decisions(self):
        """The ledger must never show a decided vote as missing just because
        nothing had recorded it yet."""
        vote = Question.objects.create(
            title="Overdue", question_text="Well?", kind=Kind.VOTE,
            closes_at=timezone.now() + timedelta(hours=1))
        Choice.objects.create(question=vote, label="Yes")
        Choice.objects.create(question=vote, label="No")
        Question.objects.filter(pk=vote.pk).update(
            closes_at=timezone.now() - timedelta(minutes=1))

        _platform()
        self.client.force_login(User.objects.create_user("u", password="pw"))
        response = self.client.get(reverse("polls:decision_ledger"))

        self.assertContains(response, "Overdue")
        self.assertTrue(Decision.objects.filter(question=vote).exists())

    def test_rows_link_to_the_results_page(self):
        _decided("Global thing")
        _platform()
        self.client.force_login(User.objects.create_user("u", password="pw"))

        response = self.client.get(reverse("polls:decision_ledger"))
        question = Question.objects.get(title="Global thing")

        self.assertContains(response, reverse(
            "polls:question_results", args=[question.kind, question.slug]))
