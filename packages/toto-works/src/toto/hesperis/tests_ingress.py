"""The --fake-data seeder.

Two things are worth asserting and one of them is a safety property: the
command must do NOTHING without the flag. `ingress_all` forwards only `full=`,
so a deploy can never reach the demo rows — but that is a property of two files
agreeing, and this is where the agreement is checked.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from toto.kanban.models import (
    Campaign, Project, Review, RewardPolicy, Submission, SubmissionResolution,
)
from toto.people.models import Person
from toto.hesperis.models import (
    AcceptedObservation, Dataset, DatasetVersion, HesperisBounty,
    HesperisCampaign,
)

User = get_user_model()


def _people(n=4):
    for i in range(n):
        user = User.objects.create_user(username=f"seed{i}", password="p")
        Person.objects.create(
            user=user, display_name=f"Seed {i}", email=f"seed{i}@x.com")


def _run(**kwargs):
    out = StringIO()
    call_command("ingress_hesperis", stdout=out, stderr=out, **kwargs)
    return out.getvalue()


class SafetyTests(TestCase):
    def test_without_the_flag_it_seeds_nothing(self):
        """The property that keeps demo rows out of a real deployment."""
        _people()
        output = _run()
        self.assertEqual(HesperisBounty.objects.count(), 0)
        self.assertEqual(Dataset.objects.count(), 0)
        self.assertIn("--fake-data", output)

    def test_full_alone_still_seeds_nothing(self):
        """`ingress_all` passes full=; that must not be enough."""
        _people()
        _run(full=True)
        self.assertEqual(HesperisBounty.objects.count(), 0)

    def test_it_refuses_without_enough_people(self):
        _people(2)
        with self.assertRaises(CommandError):
            _run(fake_data=True)


class FakeDataTests(TestCase):
    def setUp(self):
        _people()
        self.output = _run(fake_data=True)

    def test_it_creates_two_bounties(self):
        self.assertEqual(HesperisBounty.objects.count(), 2)
        self.assertEqual(HesperisCampaign.objects.count(), 1)
        self.assertEqual(Project.objects.count(), 1)

    def test_it_creates_two_datasets_one_published(self):
        self.assertEqual(Dataset.objects.count(), 2)
        self.assertEqual(DatasetVersion.objects.count(), 1)
        version = DatasetVersion.objects.get()
        self.assertEqual(version.dataset.name, "Bridges")
        self.assertTrue(version.verify())

    def test_it_shows_all_three_contribution_states(self):
        """Accepted, rejected and pending — a demo of only successes teaches less."""
        resolutions = sorted(
            Submission.objects.values_list("resolution", flat=True))
        self.assertEqual(resolutions, ["accepted", "pending", "rejected"])

    def test_an_accepted_contribution_became_an_observation(self):
        self.assertEqual(AcceptedObservation.objects.count(), 1)
        observation = AcceptedObservation.objects.get()
        self.assertEqual(
            observation.submission.resolution, SubmissionResolution.ACCEPTED)

    def test_reviews_were_recorded(self):
        self.assertEqual(Review.objects.count(), 2)

    def test_a_reward_policy_is_attached_to_the_campaign(self):
        policies = RewardPolicy.objects.all()
        self.assertEqual(policies.count(), 2)
        self.assertTrue(all(p.asset_code == "GEM" for p in policies))
        self.assertTrue(all(p.campaign_id and not p.mission_id for p in policies))

    def test_running_it_twice_does_not_duplicate(self):
        second = _run(fake_data=True)
        self.assertEqual(HesperisBounty.objects.count(), 2)
        self.assertEqual(Dataset.objects.count(), 2)
        self.assertIn("already exists", second)

    def test_the_seeded_campaign_is_findable(self):
        self.assertTrue(
            Campaign.objects.filter(name="Bridges of the Vistula").exists())
