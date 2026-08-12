"""What community standing and office do — and do not — do on the charge path.

The whole constraint in one file: an office buys HEADROOM and nothing else. It
never changes a price, never changes what is owed, and cannot make a machine the
monetary master.
"""

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from toto.assets.testing import LedgerTestCase as TestCase

from .factories import GB, make_gas_asset, make_rule, make_user, make_vault_file, price_gb_day


def _citizen(user, community=None):
    """Give this user a Person, a community and a signed constitution."""
    from toto.people.models import Person
    from toto.socialhub.models import Community, Constitution, ConstitutionSignature

    person = getattr(user, "community_profile", None) or Person.objects.create(
        user=user, display_name=user.username)
    community = community or Community.objects.create(name=f"{user.username}-guild")
    person.communities.add(community)
    constitution, _ = Constitution.objects.get_or_create(
        community=community, defaults={"title": "Charter", "body": "Be good."})
    ConstitutionSignature.objects.get_or_create(
        constitution=constitution, person=person,
        defaults={"signed_at": timezone.now()})
    return person


class OfficeChangesNoCharge(TestCase):
    """Extra limits, same taxes — asserted on the money, not on the intent."""

    def setUp(self):
        self.rule = make_rule()
        make_gas_asset()
        price_gb_day("0.5")

    def test_a_station_holder_is_charged_exactly_what_a_commoner_is(self):
        from toto.socialhub.models import Station
        from toto.vault.models import VaultUsageEvent

        from .. import services

        commoner = make_user("commoner")
        officer = make_user("officer")
        Station.objects.create(
            name="Archivist", holder=_citizen(officer),
            limit_multiplier=Decimal("10"), stipend=Decimal("5"))

        make_vault_file(commoner, 4 * GB)
        make_vault_file(officer, 4 * GB)

        services.levy_rule(self.rule)

        billed = {
            e.user_id: Decimal(str(e.quantity))
            for e in VaultUsageEvent.objects.filter(metric_code="storage.gb_day")
        }
        self.assertEqual(billed[officer.pk], billed[commoner.pk])

    def test_the_office_raises_the_limit_it_is_supposed_to_raise(self):
        from toto.quota.api import QuotaExceeded, check_quota, effective_limit, get_policy
        from toto.socialhub.models import Station
        from toto.vault.models import VaultQuotaPolicy

        VaultQuotaPolicy.objects.create(
            metric_code="storage.request", limit=Decimal("10"), mode="block")

        commoner = make_user("limited")
        officer = make_user("roomy")
        Station.objects.create(name="Archivist", holder=_citizen(officer),
                               limit_multiplier=Decimal("10"))

        policy = get_policy(VaultQuotaPolicy, "storage.request", commoner)
        self.assertEqual(effective_limit(policy, commoner), Decimal("10"))
        self.assertEqual(effective_limit(policy, officer), Decimal("100"))

        # And the gate honours it: 50 is over the commoner's cap, under theirs.
        with self.assertRaises(QuotaExceeded):
            check_quota(VaultQuotaPolicy, "storage.request", 50, commoner)
        check_quota(VaultQuotaPolicy, "storage.request", 50, officer)


class HeadWeightOnTheSweep(TestCase):
    """A community's standing arrives as the QUANTITY its provider reports."""

    def test_the_weight_resolves_the_way_the_page_says_it_does(self):
        from toto.socialhub import privileges
        from toto.socialhub.models import Community, CommunityPrivilege

        trusted = Community.objects.create(name="Trusted")
        CommunityPrivilege.objects.create(community=trusted, head_weight=Decimal("0"))
        suspect = Community.objects.create(name="Suspect")
        CommunityPrivilege.objects.create(community=suspect, head_weight=Decimal("3"))

        member = make_user("member")
        _citizen(member, suspect)
        self.assertEqual(privileges.head_weight_for(member), Decimal("3"))

        member.community_profile.communities.add(trusted)
        self.assertEqual(privileges.head_weight_for(member), Decimal("0"))


class MintHonestyTests(TestCase):
    """may_operate_mint is the USER half only — the machine half still refuses.

    Asserted so nobody later mistakes an office for authority. A privilege moves
    the user gate and only that: is_monetary_master() takes no user argument,
    and no row anywhere can change what it returns.
    """

    def test_the_office_admits_but_the_machine_refuses(self):
        from unittest import mock

        from toto.core.models import Platform
        from toto.socialhub.models import Station

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})

        member = make_user("minter")
        Station.objects.create(name="Mint Operator", holder=_citizen(member),
                               may_operate_mint=True)
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
