"""The paper: refusals run everywhere, real bytes only where reportlab lives.

The Unavailable tests patch the builder and run in every venv — including
the toto_libs gate, whose venv has no reportlab. The Render tests need the
real wheel and skip themselves where it is absent (zenobia's gate has it).
"""

from datetime import timedelta
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import render_pdf, services
from .models import Choice, Kind, PollsUsageEvent, Question

User = get_user_model()


def _decided_vote():
    vote = Question.objects.create(
        title="Adopt the charter", question_text="Well?", kind=Kind.VOTE,
        closes_at=timezone.now() + timedelta(hours=1))
    Choice.objects.create(question=vote, label="Yes")
    Choice.objects.create(question=vote, label="No")
    voter = User.objects.create_user("voter", password="pw")
    services.cast(vote, voter, vote.choices.first())
    Question.objects.filter(pk=vote.pk).update(
        closes_at=timezone.now() - timedelta(minutes=1))
    vote.refresh_from_db()
    return vote, services.record_decision(vote)


class PdfUnavailableTests(TestCase):
    """A build without reportlab answers with a sentence, not an ImportError."""

    def setUp(self):
        self.vote, self.decision = _decided_vote()
        self.client.force_login(User.objects.create_user("u", password="pw"))

    def test_the_decision_download_refuses_by_name(self):
        with mock.patch.object(render_pdf, "vote_result_pdf",
                               side_effect=render_pdf.PdfUnavailable()):
            response = self.client.get(reverse(
                "polls:decision_pdf", args=[self.vote.kind, self.vote.slug]))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertIn("reportlab", response.content.decode())

    def test_the_ledger_export_refuses_by_name(self):
        with mock.patch.object(render_pdf, "ledger_pdf",
                               side_effect=render_pdf.PdfUnavailable()):
            response = self.client.get(reverse("polls:ledger_pdf"))

        self.assertEqual(response.status_code, 503)

    def test_a_failed_render_charges_nothing(self):
        with mock.patch.object(render_pdf, "vote_result_pdf",
                               side_effect=render_pdf.PdfUnavailable()):
            self.client.get(reverse(
                "polls:decision_pdf", args=[self.vote.kind, self.vote.slug]))

        self.assertEqual(PollsUsageEvent.objects.count(), 0)

    def test_an_undecided_vote_has_no_paper(self):
        undecided = Question.objects.create(
            title="Still open", question_text="Well?", kind=Kind.VOTE,
            closes_at=timezone.now() + timedelta(hours=1))

        response = self.client.get(reverse(
            "polls:decision_pdf", args=[undecided.kind, undecided.slug]))

        self.assertEqual(response.status_code, 404)


@skipUnless(render_pdf.is_available(), "reportlab is not installed here")
class PdfRenderTests(TestCase):
    def setUp(self):
        self.vote, self.decision = _decided_vote()
        self.user = User.objects.create_user("u", password="pw")
        self.client.force_login(self.user)

    def test_the_decision_pdf_is_real_bytes_with_real_headers(self):
        response = self.client.get(reverse(
            "polls:decision_pdf", args=[self.vote.kind, self.vote.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("adopt-the-charter-decision.pdf",
                      response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_the_export_is_metered_after_success(self):
        self.client.get(reverse(
            "polls:decision_pdf", args=[self.vote.kind, self.vote.slug]))

        event = PollsUsageEvent.objects.get()
        self.assertEqual(event.metric_code, "polls.pdf")
        self.assertEqual(event.user, self.user)

    def test_the_ledger_export_honours_the_filters(self):
        response = self.client.get(reverse("polls:ledger_pdf"),
                                   {"outcome": "winner"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_the_builders_render_every_outcome_shape(self):
        """Tie and no-ballots rows must not crash the canvas arithmetic."""
        for title in ("Tie vote", "Empty vote"):
            vote = Question.objects.create(
                title=title, question_text="Well?", kind=Kind.VOTE,
                closes_at=timezone.now() + timedelta(hours=1))
            Choice.objects.create(question=vote, label="Yes")
            Choice.objects.create(question=vote, label="No")
            Question.objects.filter(pk=vote.pk).update(
                closes_at=timezone.now() - timedelta(minutes=1))
            vote.refresh_from_db()
            decision = services.record_decision(vote)
            raw = render_pdf.vote_result_pdf(vote, decision)
            self.assertTrue(raw.startswith(b"%PDF-"))
