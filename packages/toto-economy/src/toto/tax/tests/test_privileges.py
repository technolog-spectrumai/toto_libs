"""What community standing does — and does not — do on the charge path.

The whole constraint in one file: a privilege moves a USER GATE and nothing
else. It never changes a price, never changes what is owed, and cannot make a
machine the monetary master.

It used to say "an office buys HEADROOM and nothing else". Offices were
``socialhub.Station`` rows and they went in 8/2026, taking the
``limit_multiplier`` that bought the headroom with them. Rights come from
community membership now, quota limits are flat for everybody, and the half of
this file worth keeping is the half that was never about offices: a right admits
a person to a button and cannot make the button work.
"""

from decimal import Decimal

from django.urls import reverse

from toto.assets.testing import LedgerTestCase as TestCase

from .factories import GB, make_gas_asset, make_rule, make_user, make_vault_file, price_gb_day


def _privileged(user, **rights):
    """Give this user a Person and a community that grants ``rights``.

    Membership is the register that says who belongs, so joining IS the
    qualification — the same rule that used to qualify an office holder, now
    the only one.
    """
    from toto.people.models import Person
    from toto.socialhub.models import Community, CommunityPrivilege

    person = getattr(user, "community_profile", None) or Person.objects.create(
        user=user, display_name=user.username)
    community = Community.objects.create(name=f"{user.username}-guild")
    person.communities.add(community)
    if rights:
        CommunityPrivilege.objects.create(community=community, **rights)
    return person


class PrivilegeChangesNoCharge(TestCase):
    """Same taxes for everybody — asserted on the money, not on the intent."""

    def setUp(self):
        self.rule = make_rule()
        make_gas_asset()
        price_gb_day("0.5")

    def test_a_privileged_member_is_charged_exactly_what_a_commoner_is(self):
        from toto.vault.models import VaultUsageEvent

        from .. import services

        commoner = make_user("commoner")
        officer = make_user("officer")
        _privileged(officer, may_administer_communities=True)

        make_vault_file(commoner, 4 * GB)
        make_vault_file(officer, 4 * GB)

        services.levy_rule(self.rule)

        billed = {
            e.user_id: Decimal(str(e.quantity))
            for e in VaultUsageEvent.objects.filter(metric_code="storage.gb_day")
        }
        self.assertEqual(billed[officer.pk], billed[commoner.pk])

    def test_the_limit_is_the_limit_for_everybody(self):
        """Nothing buys headroom any more.

        ``Station.limit_multiplier`` scaled every limit its holder was subject
        to, and ``effective_limit`` applied it. Both went in 8/2026; this asserts
        that no privilege quietly grew a replacement.
        """
        from toto.quota.api import QuotaExceeded, check_quota, effective_limit, get_policy
        from toto.vault.models import VaultQuotaPolicy

        VaultQuotaPolicy.objects.create(
            metric_code="storage.request", limit=Decimal("10"), mode="block")

        commoner = make_user("limited")
        officer = make_user("roomy")
        _privileged(officer, may_administer_communities=True)

        policy = get_policy(VaultQuotaPolicy, "storage.request", commoner)
        self.assertEqual(effective_limit(policy, commoner), Decimal("10"))
        self.assertEqual(effective_limit(policy, officer), Decimal("10"))

        # And the gate honours it for both: 50 is over the cap either way.
        with self.assertRaises(QuotaExceeded):
            check_quota(VaultQuotaPolicy, "storage.request", 50, commoner)
        with self.assertRaises(QuotaExceeded):
            check_quota(VaultQuotaPolicy, "storage.request", 50, officer)


class MintHonestyTests(TestCase):
    """may_operate_mint is the USER half only — the machine half still refuses.

    Asserted so nobody later mistakes a privilege for authority. It moves the
    user gate and only that: ``is_monetary_master()`` takes no user argument, and
    no row anywhere can change what it returns.
    """

    def test_the_privilege_admits_but_the_machine_refuses(self):
        from unittest import mock

        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})

        member = make_user("minter")
        _privileged(member, may_operate_mint=True)
        bystander = make_user("nobody")

        self.client.force_login(bystander)
        self.assertEqual(self.client.get(reverse("mint:index")).status_code, 403)

        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("mint:index")).status_code, 200)

        with mock.patch("toto.assets.issuer.is_monetary_master",
                        return_value=False):
            response = self.client.get(reverse("mint:index"))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["is_master"])
            engrave = self.client.post(reverse("mint:issue"), {
                "name": "Pirate coin", "unit_name": "PIR",
                "total_supply": "1000", "reason": "should be refused"})
            self.assertEqual(engrave.status_code, 403)
