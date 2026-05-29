"""
Tests for toto.assets.prepaid — personal prepaid account helpers.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.assets.models import AccountType, LedgerAccount
from toto.assets.prepaid import (
    PREPAID_CODE_PREFIX,
    get_or_create_prepaid_account,
    get_prepaid_account,
    prepaid_code,
)

User = get_user_model()


class PrepaidCodeTest(TestCase):
    def test_code_format(self):
        self.assertEqual(prepaid_code(42), "user-prepaid-42")

    def test_code_uses_prefix(self):
        self.assertTrue(prepaid_code(1).startswith(PREPAID_CODE_PREFIX))


class GetOrCreatePrepaidAccountTest(TestCase):
    def setUp(self):
        # Signal creates the account on user creation; we delete it to test explicit creation
        self.user = User.objects.create_user("prepaiduser", password="x")
        LedgerAccount.objects.filter(code=prepaid_code(self.user.pk)).delete()

    def test_creates_account_on_first_call(self):
        account, created = get_or_create_prepaid_account(self.user)
        self.assertTrue(created)
        self.assertEqual(account.code, prepaid_code(self.user.pk))

    def test_idempotent_on_second_call(self):
        _, created1 = get_or_create_prepaid_account(self.user)
        _, created2 = get_or_create_prepaid_account(self.user)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(LedgerAccount.objects.filter(
            code=prepaid_code(self.user.pk)).count(), 1)

    def test_account_type_is_user(self):
        account, _ = get_or_create_prepaid_account(self.user)
        self.assertEqual(account.account_type, AccountType.USER)

    def test_account_is_active(self):
        account, _ = get_or_create_prepaid_account(self.user)
        self.assertTrue(account.active)

    def test_account_linked_to_user(self):
        account, _ = get_or_create_prepaid_account(self.user)
        self.assertEqual(account.user, self.user)

    def test_metadata_flags_prepaid(self):
        account, _ = get_or_create_prepaid_account(self.user)
        self.assertTrue(account.metadata.get("prepaid"))


class GetPrepaidAccountTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("getprepaid", password="x")

    def test_returns_none_when_account_deleted(self):
        LedgerAccount.objects.filter(code=prepaid_code(self.user.pk)).delete()
        self.assertIsNone(get_prepaid_account(self.user))

    def test_returns_account_after_creation(self):
        get_or_create_prepaid_account(self.user)
        account = get_prepaid_account(self.user)
        self.assertIsNotNone(account)
        self.assertEqual(account.code, prepaid_code(self.user.pk))


class PrepaidSignalTest(TestCase):
    def test_new_user_gets_prepaid_account_automatically(self):
        user = User.objects.create_user("signaluser", password="x")
        account = get_prepaid_account(user)
        self.assertIsNotNone(account, "Signal should have created prepaid account on user creation")
        self.assertEqual(account.code, prepaid_code(user.pk))


class BackfillCommandTest(TestCase):
    def test_backfill_creates_missing_accounts(self):
        from django.core.management import call_command
        # create user without triggering signal (direct DB)
        user = User.objects.create_user("backfillme", password="x")
        # manually delete their prepaid account to simulate pre-existing user
        LedgerAccount.objects.filter(code=prepaid_code(user.pk)).delete()
        self.assertIsNone(get_prepaid_account(user))

        call_command("backfill_prepaid_accounts", verbosity=0)

        self.assertIsNotNone(get_prepaid_account(user))

    def test_backfill_is_idempotent(self):
        from django.core.management import call_command
        User.objects.create_user("backfill2", password="x")
        call_command("backfill_prepaid_accounts", verbosity=0)
        count_before = LedgerAccount.objects.filter(
            code__startswith=PREPAID_CODE_PREFIX).count()
        call_command("backfill_prepaid_accounts", verbosity=0)
        count_after = LedgerAccount.objects.filter(
            code__startswith=PREPAID_CODE_PREFIX).count()
        self.assertEqual(count_before, count_after)
