"""The Hesperis seeder.

Two things are worth asserting and one of them is a safety property: the
command must do NOTHING without a flag, so a deployment that did not ask for
demo data never gets any.

The FLAG CHANGED in 8/2026. It was a private `--fake-data`, and the effect was
that `ingress_all` could not reach this seeder at all — that command forwards
`full=settings.FULL_INGRESS` and nothing else, so the bounty board stayed empty
even on a host that had asked for demo data in every other app. It now answers
to `--full` like the rest of them, with `--fake-data` kept as an alias.

The safety property is unchanged and is what these tests still guard: no flag
means no rows. What used to enforce it — a flag nothing could pass — enforced
rather more than that.
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
        self.assertIn("--full", output)

    def test_full_seeds_the_worked_example(self):
        """The change of 8/2026, and the reason for it.

        This asserted the opposite — that `full=` must NOT be enough — which
        made the seeder unreachable through `ingress_all`, the only automated
        path there is. Demo rows are still kept off a deployment that did not
        ask for them, but by `FULL_INGRESS` defaulting to "0", which is how
        every other app on this platform does it.
        """
        _people()
        _run(full=True)
        self.assertEqual(HesperisBounty.objects.count(), 2)

    def test_the_old_flag_still_works(self):
        """Kept as an alias so a habit or a script does not break."""
        _people()
        _run(fake_data=True)
        self.assertEqual(HesperisBounty.objects.count(), 2)

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
