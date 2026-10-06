"""The metric → pool map. The audit test is the loud one."""

from django.test import SimpleTestCase

from toto.mana.colours import (COLOUR_OF, HUE, NOT_MANA, PRICES,
                               REGEN_DEFAULTS, ROLES, TICKER)


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

    def test_geography_s_five_prices(self):
        """Two questions to an outside service draw on compute, three things
        kept draw on storage (2026-10-06). Seeds; staff own the numbers."""
        from decimal import Decimal

        expected = {
            "geography.lookup": ("compute", "0.5"),
            "geography.route": ("compute", "1"),
            "geography.pin": ("storage", "0.5"),
            "geography.zone": ("storage", "1"),
            "geography.note": ("storage", "0.2"),
        }
        for code, (role, price) in expected.items():
            with self.subTest(code=code):
                self.assertEqual(COLOUR_OF[code], role)
                self.assertEqual(PRICES[code], Decimal(price))

    def test_geography_s_metrics_are_registered_where_it_is_installed(self):
        from django.apps import apps

        from toto.quota.metrics import registry

        if not apps.is_installed("toto.geography"):
            self.skipTest("this host installs no geography")
        codes = {code for code in registry.codes() if code.startswith("geography.")}
        self.assertEqual(codes, {"geography.lookup", "geography.route", "geography.pin",
                                 "geography.zone", "geography.note"})
        for code in codes:
            self.assertEqual(registry.get(code).app_label, "geography")

    def test_every_role_has_a_ticker_and_dials(self):
        self.assertEqual(set(TICKER), set(ROLES))
        self.assertEqual(set(REGEN_DEFAULTS), set(ROLES))
        self.assertEqual(len(set(TICKER.values())), len(ROLES))


#: Where each pool's colour is written out for Tailwind, per template.
_SECURITY_TEMPLATES = (
    "mana/plugins/_chip.html",
    "mana/partials/_bar.html",
    "mana/colour.html",
    "quota/partials/_price_hint.html",
)


class HueTests(SimpleTestCase):
    def test_security_reads_cyan_and_keeps_its_ticker(self):
        """The rendering moved to cyan (2026-09-25); the ledger unit did not."""
        self.assertEqual(HUE["security"], "cyan")
        self.assertEqual(TICKER["security"], "BLUE")

    def test_the_security_branch_uses_its_own_token(self):
        """Never the theme's accent — that is whatever hue a theme picked."""
        from django.template.loader import get_template

        for name in _SECURITY_TEMPLATES:
            with self.subTest(template=name):
                source = get_template(name).template.source
                self.assertIn("security-", source)
                for line in source.splitlines():
                    if "security" in line and "accent-" in line:
                        self.fail(f"{name}: security branch names the accent "
                                  f"token: {line.strip()[:120]}")

    def test_no_pool_is_yellow(self):
        """No yellow mana: the caution token belongs to warnings, not pools."""
        from django.template.loader import get_template

        for name in _SECURITY_TEMPLATES:
            with self.subTest(template=name):
                source = get_template(name).template.source
                self.assertNotIn("caution-", source)
                self.assertNotIn("amber-", source)
