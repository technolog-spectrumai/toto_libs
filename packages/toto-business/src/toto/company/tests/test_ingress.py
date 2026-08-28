"""What `ingress_company` seeds, and what it must not.

A seed command is run against real databases, often more than once, and usually
by somebody who is not watching. So the properties worth testing are not "did it
make a company" but:

* it seeds NOTHING without ``--full`` — this is demo data, not machinery, and a
  plain deploy must not acquire a fake company;
* it is idempotent — `ingress_all` is re-run on every deploy, and a second run
  that issued the shares again would silently double the cap table;
* the ownership history is REAL — built through `services.ownership`, so every
  holding has an `OwnershipEvent` behind it. A seed that wrote `ShareHolding`
  rows directly would look identical on the page and be unprovable, which is the
  one thing this app exists to prevent;
* the chain it writes actually verifies.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.company.models import (Company, CompanyAction, CompanyMembership,
                                 Department, OwnershipEvent, Party, ShareClass,
                                 ShareHolding)
from toto.ledger.models import Ledger, LedgerEntry

#: Seeding writes real vault files, so every class that seeds needs a media root
#: it may write to. One directory for the module — the files are tiny and the
#: point is only that the write succeeds somewhere harmless.
MEDIA = tempfile.mkdtemp(prefix="company-ingress-")


def seed(full=True):
    out = StringIO()
    args = ["--full"] if full else []
    call_command("ingress_company", *args, stdout=out)
    return out.getvalue()


@override_settings(MEDIA_ROOT=MEDIA, VAULT_ROOT=MEDIA)
class WithoutFullTests(TestCase):

    def test_it_seeds_nothing(self):
        """Demo data, not machinery. A deploy must not gain a fake company."""
        seed(full=False)
        self.assertEqual(Company.objects.count(), 0)
        self.assertEqual(Ledger.objects.count(), 0)

    def test_it_says_so_rather_than_being_silent(self):
        self.assertIn("without --full", seed(full=False))


@override_settings(MEDIA_ROOT=MEDIA, VAULT_ROOT=MEDIA)
class FullSeedTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        seed()

    def test_it_creates_one_company(self):
        company = Company.objects.get()
        self.assertEqual(company.name, "Farfarele Brokker Inc.")
        self.assertEqual(company.share_capital, Decimal("100000.00"))

    def test_the_org_chart_is_a_tree_rather_than_a_row_of_boxes(self):
        board = Department.objects.get(name="Board")
        self.assertIsNone(board.parent)
        children = Department.objects.filter(parent=board)
        self.assertEqual(children.count(), 3)

    def test_the_share_classes_carry_different_weights(self):
        """One class would make the voting app's weight arithmetic a no-op."""
        weights = dict(ShareClass.objects.values_list("slug", "votes_per_unit"))
        self.assertEqual(weights["founder"], Decimal("2"))
        self.assertEqual(weights["ordinary"], Decimal("1"))

    def test_every_holding_has_an_ownership_event_behind_it(self):
        """The claim the whole app rests on.

        Issued through services.ownership, so the cap table has provenance. A
        seed that wrote ShareHolding rows directly would render identically and
        prove nothing.
        """
        live = ShareHolding.objects.filter(until__isnull=True)
        self.assertTrue(live.exists())
        self.assertEqual(OwnershipEvent.objects.count(), live.count())
        for holding in live:
            with self.subTest(party=holding.party.name):
                self.assertIsNotNone(holding.source_event_id)

    def test_the_cap_table_does_not_divide_evenly(self):
        """Deliberate: equal splits hide every rounding question."""
        units = sorted(
            ShareHolding.objects.filter(until__isnull=True)
            .values_list("units", flat=True))
        self.assertEqual(len(set(units)), len(units), "all holdings are equal")
        self.assertEqual(sum(units), Decimal("1000"))

    def test_shareholders_and_staff_are_separate_ideas(self):
        """A Party may hold without working; a member works without holding."""
        self.assertEqual(Party.objects.count(), 5)
        self.assertEqual(CompanyMembership.objects.count(), 5)
        holders = ShareHolding.objects.filter(
            until__isnull=True).values_list("party_id", flat=True)
        self.assertEqual(len(set(holders)), 4, "one person holds nothing")

    def test_the_ledger_has_more_than_a_genesis_block(self):
        """A chain of one demonstrates nothing — the property is that block two
        hashes block one."""
        self.assertEqual(Ledger.objects.count(), 1)
        self.assertGreaterEqual(LedgerEntry.objects.count(), 2)

    def test_the_recorded_action_is_actually_on_the_chain(self):
        action = CompanyAction.objects.get()
        self.assertEqual(action.title, "Founding resolution")
        self.assertIsNotNone(action.block_uid)

    def test_the_chain_verifies(self):
        from toto.company.integration import ledger as bc_ledger

        report = bc_ledger.verify_company_ledger(Company.objects.get())
        self.assertTrue(report.ok, report.detail)


@override_settings(MEDIA_ROOT=MEDIA, VAULT_ROOT=MEDIA)
class IdempotenceTests(TestCase):
    """`ingress_all` runs on every deploy. A second run must change nothing."""

    def test_running_twice_seeds_one_of_everything(self):
        seed()
        counts = (Company.objects.count(), Party.objects.count(),
                  Department.objects.count(), ShareClass.objects.count(),
                  ShareHolding.objects.count(), OwnershipEvent.objects.count(),
                  LedgerEntry.objects.count(), CompanyAction.objects.count())
        seed()
        self.assertEqual(
            (Company.objects.count(), Party.objects.count(),
             Department.objects.count(), ShareClass.objects.count(),
             ShareHolding.objects.count(), OwnershipEvent.objects.count(),
             LedgerEntry.objects.count(), CompanyAction.objects.count()),
            counts)

    def test_the_cap_table_is_not_doubled(self):
        """The failure that would matter most, and the quietest one."""
        seed()
        seed()
        total = sum(ShareHolding.objects.filter(until__isnull=True)
                    .values_list("units", flat=True))
        self.assertEqual(total, Decimal("1000"))


@override_settings(MEDIA_ROOT=MEDIA, VAULT_ROOT=MEDIA)
class SocialhubLinkTests(TestCase):
    """The two halves of the platform describe ONE organisation."""

    def test_it_points_at_the_community_of_the_same_name(self):
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.socialhub"):
            self.skipTest("built without toto.socialhub")
        from toto.socialhub.models import Community

        community = Community.objects.create(name="Farfarele Brokker Inc.")
        seed()
        company = Company.objects.get()
        self.assertTrue(company.community_ref)
        self.assertIn(company.community_ref,
                      {getattr(community, "slug", ""), str(community.pk)})

    def test_no_community_is_not_an_error(self):
        """Either seed order must work."""
        seed()
        self.assertEqual(Company.objects.count(), 1)


@override_settings(MEDIA_ROOT=MEDIA, VAULT_ROOT=MEDIA)
class CompanyFilesTests(TestCase):
    """One file per viewer this host ships, and they agree with the database.

    A demo whose documents contradict its own records teaches people not to
    trust either, so the sheet is generated FROM the share register rather than
    typed out beside it.
    """

    @classmethod
    def setUpTestData(cls):
        seed()

    def _file(self, file_type):
        from toto.vault.models import VaultFile

        return VaultFile.objects.filter(file_type=file_type).first()

    def test_there_is_a_sheet_a_document_and_a_deck(self):
        for file_type in ("sheet", "html", "pxml"):
            with self.subTest(file_type=file_type):
                self.assertIsNotNone(self._file(file_type),
                                     f"nothing seeded for {file_type}")

    def test_they_live_in_the_company_s_own_folder(self):
        from toto.vault.models import VaultFile

        company = Company.objects.get()
        for vault_file in VaultFile.objects.all():
            with self.subTest(title=vault_file.title):
                self.assertEqual(vault_file.bucket.slug,
                                 f"company-{company.slug}")
                self.assertIsNotNone(vault_file.directory_id)

    def test_the_sheet_is_a_workbook_primula_will_open(self):
        """Not merely JSON: primula refuses a .json that is not a workbook."""
        from toto.primula.sheet_format import is_sheet, loads

        raw = self._file("sheet").file.open("rb").read()
        self.assertTrue(is_sheet(raw))
        self.assertIn("Cap table", loads(raw.decode())["name"])

    def test_the_sheet_matches_the_share_register(self):
        """The claim that makes the demo coherent rather than decorative."""
        from toto.primula.sheet_format import loads

        workbook = loads(self._file("sheet").file.open("rb").read().decode())
        cells = str(workbook)
        for holding in ShareHolding.objects.filter(until__isnull=True):
            with self.subTest(party=holding.party.name):
                self.assertIn(holding.party.name, cells)

    def test_the_document_is_self_contained(self):
        """htmlview serves documents under `default-src 'none'; img-src data:`,
        so anything fetching a stylesheet or font would render unstyled."""
        html = self._file("html").file.open("rb").read().decode()
        self.assertIn("<style>", html)
        for fetching in ("<link", "src=\"http", "@import", "<script"):
            with self.subTest(pattern=fetching):
                self.assertNotIn(fetching, html)

    def test_the_document_states_the_same_registry_number(self):
        html = self._file("html").file.open("rb").read().decode()
        self.assertIn(Company.objects.get().registry_no, html)

    def test_the_deck_parses_back_as_a_presentation(self):
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.memo"):
            self.skipTest("built without toto.memo")
        from toto.memo import presentation_format as fmt

        deck = fmt.loads(self._file("pxml").file.open("rb").read().decode())
        self.assertGreaterEqual(len(deck.slides), 4)
        self.assertIn("Farfarele", deck.title)

    def test_seeding_twice_does_not_duplicate_the_files(self):
        from toto.vault.models import VaultFile

        before = VaultFile.objects.count()
        seed()
        self.assertEqual(VaultFile.objects.count(), before)
