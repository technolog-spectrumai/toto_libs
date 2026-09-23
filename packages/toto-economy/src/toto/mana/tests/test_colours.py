"""The metric → pool map. The audit test is the loud one."""

from django.test import SimpleTestCase

from toto.mana.colours import (COLOUR_OF, NOT_MANA, PRICES, REGEN_DEFAULTS,
                               ROLES, TICKER)


class MapTests(SimpleTestCase):
    def test_every_registered_metric_is_decided(self):
        """A metric nobody coloured is a metric nobody pays for.

        Registered on this host and neither mapped nor named as exempt means
        somebody added a metric and forgot mana — it would run free, silently.
        """
        from toto.quota.metrics import registry

        undecided = sorted(set(registry.codes()) - set(COLOUR_OF) - NOT_MANA)
        self.assertEqual(undecided, [],
                         "colour these in toto/mana/colours.py, or name them "
                         "in NOT_MANA on purpose")

    def test_every_colour_is_a_role(self):
        self.assertTrue(set(COLOUR_OF.values()) <= set(ROLES))

    def test_mapped_and_exempt_do_not_overlap(self):
        self.assertEqual(set(COLOUR_OF) & NOT_MANA, set())

    def test_only_mapped_metrics_are_priced(self):
        self.assertTrue(set(PRICES) <= set(COLOUR_OF))

    def test_egress_is_mapped_but_never_priced(self):
        """Cap-only by doctrine until who pays for anonymous downloads is
        decided (economy.md)."""
        self.assertEqual(COLOUR_OF["storage.egress_mb"], "storage")
        self.assertNotIn("storage.egress_mb", PRICES)

    def test_every_role_has_a_ticker_and_dials(self):
        self.assertEqual(set(TICKER), set(ROLES))
        self.assertEqual(set(REGEN_DEFAULTS), set(ROLES))
        self.assertEqual(len(set(TICKER.values())), len(ROLES))
