"""Certificates: issued once per attempt, immutable, personal, on paper."""

from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import quiz_services, render_pdf
from .quiz_models import QuizCertificate
from .tests_quizzes import _answers, _platform, _quiz

User = get_user_model()


class CertificateIssueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("taker", password="pw",
                                             first_name="Ada",
                                             last_name="Lovelace")
        self.quiz = _quiz(pass_mark=60, max_attempts=3)
        self.parts = _answers(self.quiz)

    def _take(self, *, perfect):
        selections = {self.parts["single"].pk: [self.parts["right"].pk]}
        if perfect:
            selections[self.parts["multi"].pk] = [self.parts["a"].pk,
                                                  self.parts["b"].pk]
        return quiz_services.submit_attempt(self.quiz, self.user, selections)

    def test_every_finished_attempt_gets_a_certificate(self):
        attempt = self._take(perfect=True)

        certificate = QuizCertificate.objects.get(attempt=attempt)
        content = certificate.content
        self.assertEqual(content["participant"]["full_name"], "Ada Lovelace")
        self.assertEqual(content["quiz"]["title"], "Safety basics")
        self.assertEqual(content["status"], "passed")
        self.assertEqual(content["score"], 3)
        self.assertEqual(content["attempt"], {"number": 1, "allowed": 3})
        self.assertIn("finished_at", content)
        self.assertIn("issued_at", content)

    def test_a_failed_attempt_gets_honest_paper(self):
        attempt = self._take(perfect=False)

        self.assertEqual(
            QuizCertificate.objects.get(attempt=attempt).content["status"],
            "failed")

    def test_completion_only_paper_says_completed(self):
        quiz = _quiz(title="Open training")  # no pass mark
        attempt = quiz_services.submit_attempt(quiz, self.user, {})

        self.assertEqual(
            QuizCertificate.objects.get(attempt=attempt).content["status"],
            "completed")

    def test_issue_is_idempotent_per_attempt(self):
        attempt = self._take(perfect=True)

        again = quiz_services.issue_certificate(attempt)

        self.assertEqual(QuizCertificate.objects.count(), 1)
        self.assertEqual(again.pk,
                         QuizCertificate.objects.get(attempt=attempt).pk)

    def test_a_certificate_is_never_edited_or_deleted(self):
        certificate = QuizCertificate.objects.get(
            attempt=self._take(perfect=True))

        with self.assertRaises(ValueError):
            certificate.save()
        with self.assertRaises(ValueError):
            certificate.delete()

    def test_the_content_survives_later_quiz_edits(self):
        certificate = QuizCertificate.objects.get(
            attempt=self._take(perfect=True))

        self.quiz.title = "Renamed"
        self.quiz.save()

        certificate.refresh_from_db()
        self.assertEqual(certificate.content["quiz"]["title"],
                         "Safety basics")


class CertificateDownloadTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("taker", password="pw")
        self.quiz = _quiz(pass_mark=60)
        parts = _answers(self.quiz)
        self.attempt = quiz_services.submit_attempt(
            self.quiz, self.user,
            {parts["single"].pk: [parts["right"].pk],
             parts["multi"].pk: [parts["a"].pk, parts["b"].pk]})
        self.url = reverse("polls:quiz_certificate",
                           args=[self.quiz.slug, self.attempt.number])

    def test_the_paper_is_personal(self):
        stranger = User.objects.create_user("stranger", password="pw")
        self.client.force_login(stranger)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_a_missing_renderer_refuses_by_name(self):
        self.client.force_login(self.user)

        with mock.patch.object(render_pdf, "certificate_pdf",
                               side_effect=render_pdf.PdfUnavailable()):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Content-Type"], "text/plain")

    @skipUnless(render_pdf.is_available(), "reportlab is not installed here")
    def test_the_certificate_renders_real_bytes_and_meters(self):
        from .models import PollsUsageEvent

        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("safety-basics-certificate-1.pdf",
                      response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertEqual(PollsUsageEvent.objects.get().metric_code,
                         "polls.pdf")

    @skipUnless(render_pdf.is_available(), "reportlab is not installed here")
    def test_every_status_shape_renders(self):
        for title, mark in (("Tie case", 100), ("Completion case", None)):
            quiz = _quiz(title=title, pass_mark=mark)
            attempt = quiz_services.submit_attempt(quiz, self.user, {})
            certificate = QuizCertificate.objects.get(attempt=attempt)
            raw = render_pdf.certificate_pdf(certificate)
            self.assertTrue(raw.startswith(b"%PDF-"))
