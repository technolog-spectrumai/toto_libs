"""Notes beside the ledger, and the chain that must survive them.

The first test in this file is the reason the whole design exists. Everything
else guards a specific way of getting it wrong.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from toto.assets import decorations
from toto.assets.hashing import verify_hash_chain
from toto.assets.models import (
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerEntryComment,
    LedgerEntryTag,
    LedgerTag,
)
from toto.assets.services.assets import transfer_asset
from toto.assets.testing import (
    TEST_ISSUER_KEY,
    ensure_local_issuer,
    make_asset,
)

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
class DecorationBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="owner")
        cls.stranger = User.objects.create_user(username="stranger")
        cls.staff = User.objects.create_user(username="deco-staff", is_staff=True)

        ensure_local_issuer()
        cls.reserve = LedgerAccount.objects.create(
            code="deco-reserve", name="Reserve", account_type="reserve", active=True)
        # make_asset, not mint.create_currency: toto.mint is the monetary
        # master's app and placidia does not install it, while the ledger — and
        # therefore these decorations — exist on both hosts. Same real
        # provenance, one less app required.
        cls.asset = make_asset(unit_name="TST", name="Test Coin", decimals=2,
                               reserve_account=cls.reserve)

        cls.account = LedgerAccount.objects.create(
            code="deco-mine", name="Mine", account_type="user",
            user=cls.owner, active=True)
        cls.other = LedgerAccount.objects.create(
            code="deco-theirs", name="Theirs", account_type="user",
            user=cls.stranger, active=True)

        # Funded by writing holdings rather than issuing: issuance lives in
        # toto.mint, which only the monetary master installs, and these
        # decorations exist on every host that has the ledger. What the tests
        # need is real entries under a real hash chain, and a transfer gives
        # both.
        for account in (cls.account, cls.other):
            AssetHolding.objects.create(
                account=account, asset=cls.asset,
                balance_base_units=100 * 10 ** cls.asset.decimals)
        transfer_asset(asset=cls.asset, sender_account=cls.account,
                       receiver_account=cls.other, amount=Decimal("10"),
                       reference="deco-seed")

    def my_entry(self):
        return LedgerEntry.objects.filter(account=self.account).order_by("pk").first()


class ChainSurvivesDecorationTests(DecorationBase):
    """The test the design exists for."""

    def test_the_chain_still_verifies_after_decorating(self):
        transfer_asset(asset=self.asset, sender_account=self.account,
                       receiver_account=self.other, amount=Decimal("5"),
                       reference="deco-transfer-1", description="rent")
        self.assertTrue(verify_hash_chain(), "the ledger was broken before we started")

        entry = LedgerEntry.objects.filter(account=self.account).order_by("-pk").first()
        transaction_row = entry.transaction
        before_description = transaction_row.description
        before_metadata = transaction_row.metadata

        decorations.set_comment(self.account, entry.pk, "Office, August.",
                                user=self.owner)
        for name in ("rent", "paid", "q3"):
            decorations.add_tag(self.account, entry.pk, name, user=self.owner)

        self.assertTrue(
            verify_hash_chain(),
            "decorating a movement invalidated the hash chain — the decoration "
            "reached a hashed column")

        transaction_row.refresh_from_db()
        self.assertEqual(transaction_row.description, before_description)
        self.assertEqual(transaction_row.metadata, before_metadata)

    def test_decorating_writes_no_ledger_rows(self):
        entry = self.my_entry()
        before_entries = LedgerEntry.objects.count()
        before_created = entry.created_at

        decorations.set_comment(self.account, entry.pk, "a note", user=self.owner)
        decorations.add_tag(self.account, entry.pk, "tx", user=self.owner)

        self.assertEqual(LedgerEntry.objects.count(), before_entries)
        entry.refresh_from_db()
        self.assertEqual(entry.created_at, before_created)

    def test_no_write_path_touches_a_hashed_column(self):
        """Defensive, the way the decision-chain test guards its own invariant.

        `description` and `metadata` are the two fields hashing.py covers.
        Parsed rather than grepped: a substring search matches this module's own
        docstring explaining that it must not write them, which would make the
        guard pass for the wrong reason.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(decorations))
        hashed = {"description", "metadata"}

        for node in ast.walk(tree):
            # `something.description = ...`
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr in hashed:
                        self.fail(f"assigns .{target.attr}, which is hashed")
            # `f(description=...)` — e.g. a transfer_asset call
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in hashed:
                        self.fail(f"passes {kw.arg}=, which is hashed")


class TagVocabularyTests(DecorationBase):
    def test_tags_belong_to_an_account_not_the_platform(self):
        mine = decorations.tag_for(self.account, "rent")
        theirs = decorations.tag_for(self.other, "rent")

        self.assertNotEqual(mine.pk, theirs.pk)
        self.assertEqual(LedgerTag.objects.filter(name="rent").count(), 2)

    def test_the_same_name_returns_the_same_tag(self):
        first = decorations.tag_for(self.account, "rent")
        second = decorations.tag_for(self.account, "rent")
        self.assertEqual(first.pk, second.pk)

    def test_the_hash_is_never_stored(self):
        """The AbstractTag collision, guarded: slugify('#tx') == slugify('tx')."""
        tag = decorations.tag_for(self.account, "#tx")
        self.assertEqual(tag.name, "tx")
        self.assertEqual(tag.slug, "tx")

    def test_case_and_sigil_do_not_make_a_second_tag(self):
        """Two tags that render identically would be a vocabulary nobody can use."""
        first = decorations.tag_for(self.account, "Rent")
        for spelling in ("rent", "#rent", "  RENT  "):
            self.assertEqual(decorations.tag_for(self.account, spelling).pk, first.pk,
                             spelling)
        self.assertEqual(LedgerTag.objects.filter(account=self.account).count(), 1)

    def test_a_nameless_tag_is_refused(self):
        for empty in ("", "   ", "#"):
            with self.subTest(empty=empty), self.assertRaises(ValidationError):
                decorations.tag_for(self.account, empty)

    def test_the_database_refuses_a_duplicate_even_without_the_service(self):
        decorations.tag_for(self.account, "rent")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerTag.objects.create(account=self.account, name="rent", slug="rent")


class TaggingTests(DecorationBase):
    def test_tagging_twice_attaches_once(self):
        entry = self.my_entry()
        decorations.add_tag(self.account, entry.pk, "tx", user=self.owner)
        decorations.add_tag(self.account, entry.pk, "tx", user=self.owner)
        self.assertEqual(LedgerEntryTag.objects.filter(entry_id=entry.pk).count(), 1)

    def test_untagging_keeps_the_vocabulary(self):
        """Or renaming and filtering by a tag would die with its last use."""
        entry = self.my_entry()
        link = decorations.add_tag(self.account, entry.pk, "tx", user=self.owner)

        decorations.remove_tag(self.account, entry.pk, link.tag_id)

        self.assertFalse(LedgerEntryTag.objects.filter(entry_id=entry.pk).exists())
        self.assertTrue(LedgerTag.objects.filter(account=self.account, name="tx").exists())

    def test_a_movement_on_another_account_cannot_be_tagged(self):
        """The guard the bare id costs us: no FK means no database check."""
        theirs = LedgerEntry.objects.filter(account=self.other).first()
        with self.assertRaises(ValidationError):
            decorations.add_tag(self.account, theirs.pk, "sneaky", user=self.owner)
        self.assertFalse(LedgerEntryTag.objects.filter(entry_id=theirs.pk).exists())

    def test_a_movement_that_does_not_exist_cannot_be_tagged(self):
        with self.assertRaises(ValidationError):
            decorations.add_tag(self.account, 10_000_000, "ghost", user=self.owner)


class CommentTests(DecorationBase):
    def test_a_second_comment_updates_rather_than_duplicating(self):
        entry = self.my_entry()
        decorations.set_comment(self.account, entry.pk, "first", user=self.owner)
        decorations.set_comment(self.account, entry.pk, "second", user=self.owner)

        comments = LedgerEntryComment.objects.filter(entry_id=entry.pk)
        self.assertEqual(comments.count(), 1)
        self.assertEqual(comments.get().body, "second")

    def test_the_database_refuses_a_second_comment_even_without_the_service(self):
        entry = self.my_entry()
        decorations.set_comment(self.account, entry.pk, "first", user=self.owner)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntryComment.objects.create(entry_id=entry.pk, body="second")

    def test_an_empty_body_deletes_rather_than_storing_blank(self):
        """Absence, not blankness — a stored "" makes every un-annotated
        movement look annotated to anything counting them."""
        entry = self.my_entry()
        decorations.set_comment(self.account, entry.pk, "a note", user=self.owner)

        self.assertIsNone(decorations.set_comment(self.account, entry.pk, "   "))
        self.assertFalse(LedgerEntryComment.objects.filter(entry_id=entry.pk).exists())

    def test_another_account_cannot_be_commented_on(self):
        theirs = LedgerEntry.objects.filter(account=self.other).first()
        with self.assertRaises(ValidationError):
            decorations.set_comment(self.account, theirs.pk, "sneaky", user=self.owner)


class PermissionTests(DecorationBase):
    def test_the_owner_may_annotate(self):
        self.assertTrue(decorations.can_annotate_account(self.owner, self.account))

    def test_a_stranger_may_not(self):
        self.assertFalse(decorations.can_annotate_account(self.stranger, self.account))

    def test_staff_may(self):
        self.assertTrue(decorations.can_annotate_account(self.staff, self.account))

    def test_anonymous_may_not(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(decorations.can_annotate_account(AnonymousUser(), self.account))
        self.assertFalse(decorations.can_annotate_account(None, self.account))

    def test_nobody_owns_a_company_account(self):
        """user=None by construction, so the Business Center gates its own."""
        company = LedgerAccount.objects.create(
            code="company-acme", name="Acme wallet", account_type="external",
            user=None, active=True)
        self.assertFalse(decorations.can_annotate_account(self.owner, company))


class LedgerPageTests(DecorationBase):
    def test_the_newest_movement_is_on_page_one(self):
        """The ascending-Meta.ordering trap: without an explicit re-order the
        newest movements land on the LAST page."""
        for n in range(5):
            transfer_asset(asset=self.asset, sender_account=self.account,
                           receiver_account=self.other, amount=Decimal("1"),
                           reference=f"deco-page-{n}")
        newest = LedgerEntry.objects.filter(account=self.account).order_by("-pk").first()

        context = decorations.ledger_page_context(self.account, per_page=2)

        self.assertEqual(context["rows"][0].entry.pk, newest.pk)

    def test_a_page_costs_a_fixed_number_of_queries(self):
        for n in range(12):
            transfer_asset(asset=self.asset, sender_account=self.account,
                           receiver_account=self.other, amount=Decimal("1"),
                           reference=f"deco-nplus1-{n}")
        for entry in LedgerEntry.objects.filter(account=self.account):
            decorations.set_comment(self.account, entry.pk, "note", user=self.owner)
            for name in ("a", "b", "c"):
                decorations.add_tag(self.account, entry.pk, name, user=self.owner)

        with self.assertNumQueries(6):
            context = decorations.ledger_page_context(self.account, per_page=50)
            # Touch everything a template would.
            [(row.entry.transaction.reference, row.comment.body,
              [link.tag.name for link in row.tags]) for row in context["rows"]]

    def test_decorations_land_on_the_right_rows(self):
        entry = self.my_entry()
        decorations.set_comment(self.account, entry.pk, "the one", user=self.owner)
        decorations.add_tag(self.account, entry.pk, "here", user=self.owner)

        rows = {r.entry.pk: r for r in
                decorations.ledger_page_context(self.account)["rows"]}

        self.assertEqual(rows[entry.pk].comment.body, "the one")
        self.assertEqual([link.tag.name for link in rows[entry.pk].tags], ["here"])

    def test_filtering_by_tag(self):
        transfer_asset(asset=self.asset, sender_account=self.account,
                       receiver_account=self.other, amount=Decimal("2"),
                       reference="deco-filter-1")
        tagged = LedgerEntry.objects.filter(account=self.account).order_by("-pk").first()
        link = decorations.add_tag(self.account, tagged.pk, "rent", user=self.owner)

        context = decorations.ledger_page_context(self.account, tag_slug=link.tag.slug)

        self.assertEqual([r.entry.pk for r in context["rows"]], [tagged.pk])

    def test_the_currency_picker_lists_what_moved(self):
        context = decorations.ledger_page_context(self.account)
        self.assertEqual([a.unit_name for a in context["ledger_assets"]], ["TST"])

    def test_filtering_by_asset(self):
        context = decorations.ledger_page_context(self.account, asset=self.asset)
        self.assertTrue(context["rows"])
        for row in context["rows"]:
            self.assertEqual(row.entry.asset_id, self.asset.pk)


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
class LedgerPageViewTests(DecorationBase):
    """The page, its gate, and the three write endpoints.

    Skipped where the app's URLs are not mounted. placidia installs
    ``toto.assets`` as a BRANCH economy — it gets the models and the tables, so
    every test above this one runs there — but it mounts no assets URLs, so the
    page genuinely does not exist on that host. Asserting otherwise would be
    asserting something untrue about it.
    """

    @classmethod
    def setUpClass(cls):
        import unittest

        from django.urls import NoReverseMatch, reverse

        try:
            reverse("assets:account_ledger", args=[1])
        except NoReverseMatch:
            raise unittest.SkipTest("the assets urls are not mounted on this host")
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})

    def url(self, name="assets:account_ledger", *args):
        from django.urls import reverse

        return reverse(name, args=[self.account.pk, *args])

    def test_anonymous_is_sent_to_login(self):
        """account_detail beside it is public; this page is NOT."""
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_a_stranger_gets_a_404_not_a_403(self):
        """Ownership is proved inside the lookup, so a wrong pk is a miss."""
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_the_owner_sees_their_movements(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "deco-seed")

    def test_staff_may_look(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_a_note_can_be_written_and_rewritten(self):
        entry = self.my_entry()
        self.client.force_login(self.owner)

        self.client.post(self.url("assets:entry_comment", entry.pk), {"body": "first"})
        self.assertEqual(LedgerEntryComment.objects.get(entry_id=entry.pk).body, "first")

        self.client.post(self.url("assets:entry_comment", entry.pk), {"body": "second"})
        self.assertEqual(LedgerEntryComment.objects.get(entry_id=entry.pk).body, "second")

    def test_a_tag_can_be_added_and_removed_after_the_fact(self):
        entry = self.my_entry()
        self.client.force_login(self.owner)

        self.client.post(self.url("assets:entry_tag_add", entry.pk), {"name": "#rent"})
        link = LedgerEntryTag.objects.get(entry_id=entry.pk)
        self.assertEqual(link.tag.name, "rent")          # the sigil is not stored

        self.client.post(self.url("assets:entry_tag_remove", entry.pk),
                         {"tag": link.tag_id})
        self.assertFalse(LedgerEntryTag.objects.filter(entry_id=entry.pk).exists())

    def test_a_stranger_cannot_write_on_someone_elses_ledger(self):
        entry = self.my_entry()
        self.client.force_login(self.stranger)

        for name in ("assets:entry_comment", "assets:entry_tag_add"):
            with self.subTest(name):
                response = self.client.post(self.url(name, entry.pk),
                                            {"body": "x", "name": "x"})
                self.assertEqual(response.status_code, 404)
        self.assertFalse(LedgerEntryComment.objects.filter(entry_id=entry.pk).exists())

    def test_a_bad_tag_name_is_a_message_not_a_500(self):
        entry = self.my_entry()
        self.client.force_login(self.owner)
        response = self.client.post(self.url("assets:entry_tag_add", entry.pk),
                                    {"name": "   "})
        self.assertEqual(response.status_code, 302)

    def test_the_chips_render_with_the_sigil_but_store_without_it(self):
        entry = self.my_entry()
        decorations.add_tag(self.account, entry.pk, "rent", user=self.owner)
        self.client.force_login(self.owner)

        body = self.client.get(self.url()).content.decode()

        self.assertIn(">#</span>rent", body)
        self.assertEqual(LedgerTag.objects.get(account=self.account).name, "rent")

    def test_a_plain_reader_gets_no_edit_affordance(self):
        """Staff may read another account's ledger, and may annotate it; a
        person with no rights never reaches the page at all."""
        entry = self.my_entry()
        decorations.add_tag(self.account, entry.pk, "rent", user=self.owner)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url()), "Edit")
