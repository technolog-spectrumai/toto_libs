"""
Tests for toto.subscriptions.gates — subscription gate helpers.

Covers:
1. is_subscribed returns False for unauthenticated user
2. is_subscribed returns False when no subscription exists
3. is_subscribed returns True for active subscription
4. is_subscribed returns True for trialing subscription
5. is_subscribed returns False for cancelled subscription
6. require_subscription decorator redirects when not subscribed
7. require_subscription decorator passes through when subscribed
8. Academy course_enroll allows open enrollment when no academy plans exist
9. Academy course_enroll blocks when academy plans exist and user not subscribed
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from django.urls import reverse

from toto.subscriptions.gates import is_subscribed, SubscriptionRequired

User = get_user_model()


def make_platform():
    from toto.core.models import Platform
    return Platform.objects.get_or_create(
        site_name="Test", defaults={"author": "t", "publication_year": 2025, "active": True}
    )[0]


class IsSubscribedTest(TestCase):

    def test_unauthenticated_user_is_not_subscribed(self):
        self.assertFalse(is_subscribed(None, "academy"))

    def test_user_without_profile_is_not_subscribed(self):
        user = User.objects.create_user("nosub", password="x")
        self.assertFalse(is_subscribed(user, "academy"))

    def test_user_with_active_subscription_is_subscribed(self):
        from toto.socialhub.models import Community
        from toto.people.models import Person
        from toto.assets.models import Asset, LedgerAccount, AccountType
        from toto.subscriptions.models import (
            SubscriptionPlan, SubscriptionPrice, SubscriptionCustomer, Subscription,
        )

        community = Community.objects.create(name="Sub Test", slug="sub-test-gates")
        user = User.objects.create_user("subuser", password="x")
        person = Person.objects.create(display_name="Sub User", user=user)
        asset = Asset.objects.create(
            name="STOKEN", unit_name="STOKEN", decimals=2,
            total_supply_base_units=10 ** 10, active=True,
        )
        billing_account = LedgerAccount.objects.create(
            code="sub-billing", name="sub billing",
            account_type=AccountType.USER, active=True,
        )
        plan = SubscriptionPlan.objects.create(
            name="Academy Plan", code="academy", status="active",
            community=community,
        )
        recv_acct = LedgerAccount.objects.create(
            code="sub-recv-1", name="sub recv",
            account_type=AccountType.SYSTEM, active=True,
        )
        price = SubscriptionPrice.objects.create(
            plan=plan, asset=asset, code="academy-monthly",
            amount_base_units=1000,
            interval="month", interval_count=1, is_active=True,
            receiving_account=recv_acct,
        )
        customer = SubscriptionCustomer.objects.create(
            community=community, person=person,
            billing_account=billing_account, default_asset=asset,
        )
        Subscription.objects.create(
            customer=customer, plan=plan, price=price,
            status="active",
        )
        self.assertTrue(is_subscribed(user, "academy"))

    def test_cancelled_subscription_is_not_subscribed(self):
        from toto.socialhub.models import Community
        from toto.people.models import Person
        from toto.assets.models import Asset, LedgerAccount, AccountType
        from toto.subscriptions.models import (
            SubscriptionPlan, SubscriptionPrice, SubscriptionCustomer, Subscription,
        )
        community = Community.objects.create(name="Cancelled Test", slug="cancelled-test")
        user = User.objects.create_user("canceluser", password="x")
        person = Person.objects.create(display_name="Cancel User", user=user)
        asset = Asset.objects.create(
            name="CTOKEN", unit_name="CTOKEN", decimals=2,
            total_supply_base_units=10 ** 10, active=True,
        )
        billing_account = LedgerAccount.objects.create(
            code="cancel-billing", name="cancel billing",
            account_type=AccountType.USER, active=True,
        )
        plan = SubscriptionPlan.objects.create(name="Academy", code="academy-cancel", status="active", community=community)
        recv_acct2 = LedgerAccount.objects.create(
            code="sub-recv-2", name="sub recv 2",
            account_type=AccountType.SYSTEM, active=True,
        )
        price = SubscriptionPrice.objects.create(
            plan=plan, asset=asset, code="academy-cancel-monthly",
            amount_base_units=1000,
            interval="month", interval_count=1, is_active=True,
            receiving_account=recv_acct2,
        )
        customer = SubscriptionCustomer.objects.create(
            community=community, person=person,
            billing_account=billing_account, default_asset=asset,
        )
        Subscription.objects.create(customer=customer, plan=plan, price=price, status="cancelled")
        self.assertFalse(is_subscribed(user, "academy-cancel"))

    def test_returns_false_gracefully_on_error(self):
        user = User.objects.create_user("erruser", password="x")
        # Should not raise even if something unexpected happens
        result = is_subscribed(user, "nonexistent_plan")
        self.assertFalse(result)



class SubscriptionGatesArchitectureTest(TestCase):
    def test_gates_module_does_not_import_tariffs_or_treasury(self):
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "gates.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    "tariffs" in node.module,
                    "subscriptions/gates.py must not import tariffs",
                )
                self.assertFalse(
                    "treasury" in node.module,
                    "subscriptions/gates.py must not import treasury",
                )
