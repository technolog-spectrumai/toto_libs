"""What the mint tab shows an operator before they decide to mint.

Tested without HTTP on purpose: these are the numbers a monetary decision is
made on, and they should not need a template, a request or a logged-in user to
be checked.
"""

from decimal import Decimal

from toto.assets.models import AccountType, LedgerAccount
from toto.assets.services.assets import distribute_asset, engrave_currency
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint.summary import chain_summary, currency_rows, history
from toto.mint.services import burn, create_currency


def _account(code, kind=AccountType.RESERVE):
    return LedgerAccount.objects.create(code=code, name=code,
                                        account_type=kind)


class CurrencyRowTests(TestCase):
    def setUp(self):
        super().setUp()
        self.reserve = _account("reserve")
        self.asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")

    def _row(self):
        return next(r for r in currency_rows()
                    if r.asset.pk == self.asset.pk)

    def test_a_fully_minted_currency_reads_correctly(self):
        row = self._row()

        self.assertEqual(row.supply, Decimal("1000.00"))
        self.assertEqual(row.max_supply, Decimal("1000.00"))
        self.assertEqual(row.unminted, Decimal("0.00"))
        self.assertEqual(row.reserve, Decimal("1000.00"))
        self.assertTrue(row.fully_minted)
        self.assertEqual(row.events, 1)

    def test_burning_opens_room_to_mint_again(self):
        burn(asset=self.asset, amount=Decimal("400"), reason="withdrawn")
        row = self._row()

        self.assertEqual(row.supply, Decimal("600.00"))
        self.assertEqual(row.unminted, Decimal("400.00"))
        self.assertFalse(row.fully_minted)
        self.assertEqual(row.events, 2)

    def test_distributing_moves_units_out_of_the_reserve_only(self):
        holder = _account("holder", AccountType.USER)
        distribute_asset(asset=self.asset, recipient_account=holder,
                         amount=Decimal("250"), reference="give")
        row = self._row()

        self.assertEqual(row.supply, Decimal("1000.00"))
        self.assertEqual(row.reserve, Decimal("750.00"))
        self.assertFalse(row.reserve_matches_supply)

    def test_an_engraved_but_unminted_currency_shows_a_full_headroom(self):
        engrave_currency(name="Zloty", unit_name="TPLN",
                         max_supply=Decimal("500"), decimals=2)
        row = next(r for r in currency_rows() if r.asset.unit_name == "TPLN")

        self.assertEqual(row.supply, Decimal("0.00"))
        self.assertEqual(row.unminted, Decimal("500.00"))
        self.assertEqual(row.reserve, Decimal("0.00"))
        self.assertEqual(row.events, 0)

    def test_mirrors_are_not_listed(self):
        # A mirror is somebody else's identity; there is nothing here that
        # could be minted or burned.
        from toto.assets.models import Asset

        Asset.objects.filter(pk=self.asset.pk).update(is_mirror=True)
        self.assertEqual(
            [r for r in currency_rows() if r.asset.pk == self.asset.pk], [])


class ChainSummaryTests(TestCase):
    def setUp(self):
        super().setUp()
        self.reserve = _account("reserve")

    def test_an_empty_chain_reports_an_empty_head(self):
        summary = chain_summary()

        self.assertEqual(summary["length"], 0)
        self.assertEqual(summary["head_hash"], "")
        self.assertEqual(summary["next_sequence"], 0)
        self.assertTrue(summary["ok"])

    def test_the_head_and_next_sequence_track_the_chain(self):
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")
        burn(asset=asset, amount=Decimal("100"), reason="dust")

        summary = chain_summary()
        self.assertEqual(summary["length"], 2)
        self.assertEqual(summary["next_sequence"], 2)
        self.assertEqual(summary["head_hash"], summary["head"].event_hash)
        self.assertTrue(summary["ok"], summary["findings"])

    def test_a_broken_chain_is_reported_with_its_findings(self):
        from toto.mint.models import CurrencyMintEvent

        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")
        event = CurrencyMintEvent.objects.get(asset=asset)
        CurrencyMintEvent.objects.filter(pk=event.pk).update(
            payload=dict(event.payload, amount_base_units=1))

        summary = chain_summary()
        self.assertFalse(summary["ok"])
        self.assertTrue(summary["findings"])


class HistoryTests(TestCase):
    def test_the_history_is_newest_first_and_carries_both_numberings(self):
        reserve = _account("reserve")
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=reserve, reference="open")
        burn(asset=asset, amount=Decimal("100"), reason="dust")

        events = list(history())
        self.assertEqual([e.sequence for e in events], [1, 0])
        self.assertEqual(events[0].kind, "burn")
        # Both numberings: the position in the chain, and the chain-wide name.
        self.assertTrue(events[0].event_hash.startswith("tmev1:"))
        self.assertEqual(events[0].prev_hash, events[1].event_hash)
