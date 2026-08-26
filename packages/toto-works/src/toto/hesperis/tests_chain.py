"""Hesperis without a chain engine.

`toto.ledger` is HOST-OWNED — it lives in a host's own `toto/` portion, not in
this wheel. So the suite has to prove the half that is this package's problem:
that a host which installs no chain engine still accepts observations and still
publishes releases, and that asking for a verification says "none" rather than
raising or reporting a broken chain.

The other half — that the chain actually records and actually catches a rewrite
— can only be asserted where an engine exists, and is in zenobia's own
`zenobia/tests/test_hesperis_chain.py`.
"""

from django.apps import apps
from django.test import TestCase

from toto.hesperis import services
from toto.hesperis.integration import ledger as seam
from toto.hesperis.models import Dataset

from .tests_domain import HesperisTestBase


class NoChainEngineTests(HesperisTestBase):

    def setUp(self):
        super().setUp()
        self.dataset = Dataset.objects.create(
            campaign=self.pcampaign, name="Bridges", slug="bridges")

    def test_this_harness_really_has_no_engine(self):
        """Guards the other tests: if a ledger appeared here they would pass
        for the wrong reason."""
        self.assertFalse(apps.is_installed("toto.ledger"))
        self.assertFalse(seam.available())

    def test_an_observation_is_still_accepted(self):
        submission = self._contribute_and_resolve()
        observation = services.accept(submission)
        self.assertIsNotNone(observation.pk)

    def test_a_version_is_still_frozen(self):
        services.accept(self._contribute_and_resolve())
        version = services.freeze(self.dataset, by=self.author)
        self.assertTrue(version.manifest_hash)
        self.assertTrue(version.verify())

    def test_recording_is_a_no_op_rather_than_an_error(self):
        observation = services.accept(self._contribute_and_resolve())
        self.assertEqual(seam.record_observation(observation), [])
        version = services.freeze(self.dataset, by=self.author)
        self.assertIsNone(seam.record_version(version))

    def test_verification_reports_nothing_rather_than_broken(self):
        """A page must not render 'this dataset's history does not verify' on a
        host that was never going to have a history."""
        services.accept(self._contribute_and_resolve())
        self.assertIsNone(seam.verify_dataset(self.dataset))
        self.assertIsNone(seam.dataset_ledger(self.dataset))


class PayloadDigestTests(TestCase):
    """The digest is what makes an edit detectable, so it has to be stable."""

    def test_key_order_does_not_change_it(self):
        self.assertEqual(
            seam.payload_digest({"a": 1, "b": 2}),
            seam.payload_digest({"b": 2, "a": 1}))

    def test_a_changed_value_changes_it(self):
        self.assertNotEqual(
            seam.payload_digest({"a": 1}), seam.payload_digest({"a": 2}))

    def test_none_and_empty_differ(self):
        self.assertNotEqual(seam.payload_digest(None), seam.payload_digest({}))
