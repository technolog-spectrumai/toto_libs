"""The staff form: who may open a vote, and what the form refuses."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .core import Revisability
from .electorate_models import Electorate, ElectorateMember
from .models import Kind, Question

User = get_user_model()

def _platform():
    from toto.core.models import Platform

    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})



def _electorate(user):
    """A configured roll for the form to freeze from — since stage 5 an
    electorate is data, not a registry key."""
    roll = Electorate.objects.create(name="Everyone")
    ElectorateMember.objects.create(electorate=roll, user=user)
    return roll


def _post_data(electorate=None, **overrides):
    opens = timezone.now()
    closes = opens + timedelta(days=7)
    data = {
        "title": "Adopt the charter",
        "question_text": "Shall the charter be adopted?",
        "body": "The case for and against.",
        "decision_header": "Adopts the charter as written.",
        "electorate": electorate.pk if electorate else "",
        "opens_at_0": opens.date().isoformat(),
        "opens_at_1": opens.strftime("%H:%M"),
        "closes_at_0": closes.date().isoformat(),
        "closes_at_1": closes.strftime("%H:%M"),
        "visibility": "on_close",
        "choices_text": "For: adopt it\nAgainst",
    }
    data.update(overrides)
    return data


class VoteCreateAccessTests(TestCase):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("polls:vote_create"))

        self.assertEqual(response.status_code, 302)

    def test_a_civilian_gets_a_403_not_a_redirect(self):
        self.client.force_login(User.objects.create_user("civ", password="pw"))

        self.assertEqual(
            self.client.get(reverse("polls:vote_create")).status_code, 403)
        # The gate refuses before the form is even bound, so the payload's
        # shape is irrelevant here.
        self.assertEqual(
            self.client.post(reverse("polls:vote_create"),
                             _post_data()).status_code, 403)


class VoteCreateFormTests(TestCase):
    def setUp(self):
        _platform()
        self.staff = User.objects.create_user("op", password="pw",
                                              is_staff=True)
        self.roll = _electorate(self.staff)
        self.client.force_login(self.staff)

    def test_a_valid_post_creates_a_final_vote(self):
        response = self.client.post(reverse("polls:vote_create"), _post_data(self.roll))

        self.assertEqual(response.status_code, 302)
        vote = Question.objects.get(title="Adopt the charter")
        self.assertEqual(vote.kind, Kind.VOTE)
        self.assertEqual(vote.revisability, Revisability.FINAL)
        self.assertEqual(vote.created_by, self.staff)
        self.assertEqual(vote.electorate, self.roll)
        self.assertEqual(vote.decision_header,
                         "Adopts the charter as written.")
        # The register was frozen at open — stage 5.
        self.assertEqual(vote.roll.count(), 1)
        self.assertEqual(
            [(c.label, c.text) for c in vote.choices.all()],
            [("For", "adopt it"), ("Against", "")])

    def test_one_option_is_refused(self):
        response = self.client.post(reverse("polls:vote_create"),
                                    _post_data(self.roll, choices_text="Only one"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["choices_text"])
        self.assertFalse(Question.objects.exists())

    def test_duplicate_labels_are_refused(self):
        response = self.client.post(reverse("polls:vote_create"),
                                    _post_data(self.roll, choices_text="Yes\nYes"))

        self.assertTrue(response.context["form"].errors["choices_text"])

    def test_closing_before_opening_is_refused(self):
        past = timezone.now() - timedelta(days=1)
        response = self.client.post(reverse("polls:vote_create"), _post_data(
            self.roll,
            closes_at_0=past.date().isoformat(),
            closes_at_1=past.strftime("%H:%M")))

        self.assertTrue(response.context["form"].errors["closes_at"])

    def test_a_missing_electorate_is_refused(self):
        """A vote with no roll to freeze is not a vote."""
        response = self.client.post(reverse("polls:vote_create"),
                                    _post_data(None))

        self.assertTrue(response.context["form"].errors["electorate"])

    def test_no_deadline_means_manual_close(self):
        response = self.client.post(reverse("polls:vote_create"), _post_data(
            self.roll, closes_at_0="", closes_at_1=""))

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(
            Question.objects.get(title="Adopt the charter").closes_at)
