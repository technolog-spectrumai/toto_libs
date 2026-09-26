"""Faucets as the record of every increase (2026-09-26): grouped and filtered
by Community, and the transactions page filtered the same way."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.assets.models import Faucet, FaucetMember, FaucetPayout
from toto.assets.services import faucets
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class CommunityFaucetTests(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test", defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        from toto.assets.models import Asset

        self.asr = Asset.objects.get(unit_name="ASR")
        self.staff = User.objects.create_user("stan", password="pw", is_staff=True)
        self.guild = Community.objects.create(name="Guild", slug="guild")
        self.lodge = Community.objects.create(name="Lodge", slug="lodge")
        self.ada = self.member("ada", self.guild)
        self.bob = self.member("bob", self.lodge)
        self.guild_faucet = Faucet.objects.create(name="Guild pay", asset=self.asr, active=True,
                                                  community=self.guild)
        self.lodge_faucet = Faucet.objects.create(name="Lodge pay", asset=self.asr, active=True,
                                                  community=self.lodge)
        FaucetMember.objects.create(faucet=self.guild_faucet, user=self.ada, amount_per_hour=Decimal("1"))
        FaucetMember.objects.create(faucet=self.lodge_faucet, user=self.bob, amount_per_hour=Decimal("2"))

    def member(self, name, community):
        user = User.objects.create_user(name, password="pw")
        Person.objects.create(user=user, display_name=name).communities.add(community)
        return user

    def test_member_payouts_name_their_faucet_recipient_and_community(self):
        report = faucets.run_hour()
        self.assertEqual(report.paid, 2)
        payout = FaucetPayout.objects.get(member__user=self.ada)
        self.assertEqual((payout.faucet, payout.recipient, payout.source, payout.community),
                         (self.guild_faucet, self.ada, "members", self.guild))
        # A second run: nothing new, and the constraint is on (faucet, recipient, period).
        self.assertEqual(faucets.run_hour().paid, 0)
        self.assertEqual(FaucetPayout.objects.filter(recipient=self.ada, faucet=self.guild_faucet).count(), 1)

    def test_faucets_are_grouped_and_filtered_by_community_and_source(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("assets:faucet_list"))
        self.assertContains(page, 'data-testid="faucet-group"')
        self.assertContains(page, "Guild pay")
        self.assertContains(page, "Lodge pay")
        only_guild = self.client.get(reverse("assets:faucet_list"), {"community": self.guild.pk})
        self.assertContains(only_guild, "Guild pay")
        self.assertNotContains(only_guild, "Lodge pay")
        manual = self.client.get(reverse("assets:faucet_list"), {"source": "manual"})
        self.assertNotContains(manual, "Guild pay")

    def test_the_faucet_page_lists_payouts_with_source_amount_recipient_and_time(self):
        faucets.run_hour()
        self.client.force_login(self.staff)
        page = self.client.get(reverse("assets:faucet_detail", args=[self.guild_faucet.pk]))
        self.assertContains(page, 'data-testid="faucet-payouts"')
        self.assertContains(page, "ada")
        self.assertContains(page, "Named members, hourly")
        self.assertNotContains(page, "bob")
        # A member sees their own rows only.
        self.client.force_login(self.bob)
        page = self.client.get(reverse("assets:faucet_detail", args=[self.guild_faucet.pk]))
        self.assertNotContains(page, "ada")

    def test_transactions_filter_by_community(self):
        faucets.run_hour()
        self.client.force_login(self.staff)
        page = self.client.get(reverse("assets:transaction_list"), {"community": self.guild.pk})
        self.assertEqual(page.status_code, 200)
        refs = [tx.reference for tx in page.context["transactions"]]
        ada_member = FaucetMember.objects.get(user=self.ada)
        bob_member = FaucetMember.objects.get(user=self.bob)
        self.assertIn(f"faucet-{ada_member.pk}-{faucets.period_label()}", refs)
        self.assertNotIn(f"faucet-{bob_member.pk}-{faucets.period_label()}", refs)
        # Only rows touching a Guild member's account: bob's mana fills are not there.
        self.assertFalse(any(ref.startswith(f"mana:") and f":{self.bob.pk}:" in ref for ref in refs))
        every = self.client.get(reverse("assets:transaction_list")).context["total_count"]
        self.assertGreater(every, len(refs))
        flow = self.client.get(reverse("assets:ledger_flow_data"), {"community": self.lodge.pk})
        self.assertEqual(flow.status_code, 200)
