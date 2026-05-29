"""
Tests for toto.socialhub.treasury — community treasury account helpers.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.assets.models import AccountType, LedgerAccount
from toto.socialhub.models import Community
from toto.socialhub.treasury import (
    TREASURY_CODE_PREFIX,
    get_or_create_treasury_account,
    get_treasury_account,
    treasury_code,
)

User = get_user_model()


_community_counter = 0

def make_community(name="Test Community"):
    global _community_counter
    _community_counter += 1
    slug = f"community-{_community_counter}"
    return Community.objects.create(name=name, slug=slug)


class TreasuryCodeTest(TestCase):
    def test_code_format(self):
        self.assertEqual(treasury_code(7), "community-treasury-7")

    def test_uses_prefix(self):
        self.assertTrue(treasury_code(1).startswith(TREASURY_CODE_PREFIX))


class GetOrCreateTreasuryAccountTest(TestCase):
    def setUp(self):
        self.community = make_community("Finance Guild")
        # Signal already created the account; delete it to test explicit creation
        LedgerAccount.objects.filter(code=treasury_code(self.community.pk)).delete()

    def test_creates_account_on_first_call(self):
        account, created = get_or_create_treasury_account(self.community)
        self.assertTrue(created)
        self.assertEqual(account.code, treasury_code(self.community.pk))

    def test_idempotent_on_second_call(self):
        _, c1 = get_or_create_treasury_account(self.community)
        _, c2 = get_or_create_treasury_account(self.community)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(LedgerAccount.objects.filter(
            code=treasury_code(self.community.pk)).count(), 1)

    def test_account_type_is_system(self):
        account, _ = get_or_create_treasury_account(self.community)
        self.assertEqual(account.account_type, AccountType.SYSTEM)

    def test_account_is_active(self):
        account, _ = get_or_create_treasury_account(self.community)
        self.assertTrue(account.active)

    def test_metadata_flags_treasury(self):
        account, _ = get_or_create_treasury_account(self.community)
        self.assertTrue(account.metadata.get("treasury"))
        self.assertEqual(account.metadata.get("community_pk"), self.community.pk)


class GetTreasuryAccountTest(TestCase):
    def setUp(self):
        self.community = make_community("No Treasury Yet")

    def test_returns_none_when_deleted(self):
        LedgerAccount.objects.filter(
            code=treasury_code(self.community.pk)).delete()
        self.assertIsNone(get_treasury_account(self.community))

    def test_returns_account_after_creation(self):
        get_or_create_treasury_account(self.community)
        account = get_treasury_account(self.community)
        self.assertIsNotNone(account)


class TreasurySignalTest(TestCase):
    def test_new_community_gets_treasury_automatically(self):
        community = make_community("Signal Community")
        account = get_treasury_account(community)
        self.assertIsNotNone(account, "Signal should create treasury on community creation")


class BackfillTreasuryCommandTest(TestCase):
    def test_backfill_creates_missing_accounts(self):
        from django.core.management import call_command
        community = make_community("Old Community")
        LedgerAccount.objects.filter(code=treasury_code(community.pk)).delete()
        self.assertIsNone(get_treasury_account(community))

        call_command("backfill_treasury_accounts", verbosity=0)

        self.assertIsNotNone(get_treasury_account(community))

    def test_backfill_is_idempotent(self):
        from django.core.management import call_command
        make_community("Idempotent Community")
        call_command("backfill_treasury_accounts", verbosity=0)
        count_before = LedgerAccount.objects.filter(
            code__startswith=TREASURY_CODE_PREFIX).count()
        call_command("backfill_treasury_accounts", verbosity=0)
        count_after = LedgerAccount.objects.filter(
            code__startswith=TREASURY_CODE_PREFIX).count()
        self.assertEqual(count_before, count_after)
