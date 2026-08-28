"""Hosted-git storage: the sampler, the cap reconciler, and the levy.

The forge is mocked at the ``client.*`` seams throughout (house style — no
gitea container). The one end-to-end class runs the REAL tax sweep against
the real assets/tariffs stack and is skipped on hosts without the economy:
the provider itself is pure toto-repo, the sweep is not.
"""

from unittest import mock, skipUnless

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.gitea import client
from toto.gitea.errors import GiteaError
from toto.gitea.models import GiteaAccount, GiteaForgeSample
from toto.gitea.tasks import gitea_sample_storage
from toto.gitea.taxes import GiteaStorageLevy

GB = 2 ** 30
FORGE = {"GITEA_ENABLED": True, "GITEA_SVC_PASSWORD": "svc-pw",
         "GITEA_INTERNAL_URL": "http://gitea:3000"}

ECONOMY = django_apps.is_installed("toto.tax")
if ECONOMY:
    from toto.assets.testing import LedgerTestCase as _LedgerBase
else:                                  # the class body still needs A base
    _LedgerBase = TestCase


def _resp(status, payload=None, text=""):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    r.text = text
    return r


def _repo(owner, name, size_kib):
    return {"owner": {"login": owner}, "name": name, "size": size_kib}


@override_settings(**FORGE)
class IterAllReposTests(TestCase):
    @mock.patch("toto.gitea.client.requests.request")
    def test_pagination_walks_every_page_once(self, req):
        first = [_repo("alice", f"r{i}", 1) for i in range(50)]
        second = [_repo("bob", "tail", 1) for _ in range(3)]
        req.side_effect = [_resp(200, {"data": first}),
                           _resp(200, {"data": second})]
        repos = list(client.iter_all_repos())
        self.assertEqual(len(repos), 53)
        self.assertEqual(req.call_count, 2)
        # Admin pass over the WHOLE forge: private repos included, page 2 asked
        # for exactly once because page 1 came back full.
        for number, call in enumerate(req.call_args_list, start=1):
            self.assertEqual(call.kwargs["params"]["private"], "true")
            self.assertEqual(call.kwargs["params"]["page"], number)

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_short_first_page_is_the_only_page(self, req):
        req.side_effect = [_resp(200, {"data": [_repo("alice", "only", 1)]})]
        self.assertEqual(len(list(client.iter_all_repos())), 1)
        self.assertEqual(req.call_count, 1)

    @mock.patch("toto.gitea.client.requests.request")
    def test_an_empty_forge_yields_nothing(self, req):
        req.side_effect = [_resp(200, {"data": []})]
        self.assertEqual(list(client.iter_all_repos()), [])

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_forge_error_raises_not_truncates(self, req):
        # A 500 mid-walk must never look like "fewer repositories" — a
        # truncated sample would quietly under-bill everyone after the break.
        req.side_effect = [_resp(500, text="boom")]
        with self.assertRaises(GiteaError):
            list(client.iter_all_repos())


@override_settings(**FORGE)
class SetMaxRepoCreationTests(TestCase):
    @mock.patch("toto.gitea.client.requests.request")
    def test_login_name_and_source_id_are_echoed(self, req):
        # Gitea's EditUserOption RESETS these two when omitted; the whole
        # reason the helper fetches before patching.
        req.side_effect = [
            _resp(200, {"login": "alice", "login_name": "ldap-alice",
                        "source_id": 7}),
            _resp(200),
        ]
        client.set_max_repo_creation("alice", 0)
        patch_call = req.call_args_list[1]
        self.assertEqual(patch_call.args[0], "PATCH")
        self.assertIn("/admin/users/alice", patch_call.args[1])
        self.assertEqual(patch_call.kwargs["json"], {
            "login_name": "ldap-alice", "source_id": 7,
            "max_repo_creation": 0})

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_local_user_falls_back_to_the_login(self, req):
        req.side_effect = [_resp(200, {"login": "bob", "login_name": "",
                                       "source_id": 0}),
                           _resp(204)]
        client.set_max_repo_creation("bob", -1)
        sent = req.call_args_list[1].kwargs["json"]
        self.assertEqual(sent["login_name"], "bob")
        self.assertEqual(sent["max_repo_creation"], -1)


@override_settings(**FORGE)
class SamplerTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.account = GiteaAccount.objects.create(user=self.alice,
                                                   username="alice")

    def test_bytes_are_attributed_and_kib_scaled(self):
        repos = [_repo("alice", "notes", 2048),      # 2 MiB
                 _repo("alice", "code", 1024),       # 1 MiB
                 _repo("some-org", "shared", 4096)]  # nobody's
        with mock.patch("toto.gitea.client.iter_all_repos",
                        return_value=iter(repos)):
            summary = gitea_sample_storage()
        self.account.refresh_from_db()
        self.assertEqual(self.account.storage_bytes, 3 * 1024 * 1024)
        self.assertIsNotNone(self.account.storage_sampled_at)
        # Skip and surface: the org's bytes are billed to nobody but the
        # number is kept where staff can see it.
        sample = GiteaForgeSample.objects.get()
        self.assertEqual(sample.unattributed_bytes, 4096 * 1024)
        self.assertEqual(sample.total_bytes, 7168 * 1024)
        self.assertTrue(summary["sampled"])

    def test_shedding_everything_zeroes_the_column(self):
        self.account.storage_bytes = 5 * GB
        self.account.save(update_fields=["storage_bytes"])
        with mock.patch("toto.gitea.client.iter_all_repos",
                        return_value=iter([])):
            gitea_sample_storage()
        self.account.refresh_from_db()
        self.assertEqual(self.account.storage_bytes, 0)

    def test_a_dead_forge_keeps_the_last_sample(self):
        self.account.storage_bytes = 5 * GB
        self.account.save(update_fields=["storage_bytes"])
        with mock.patch("toto.gitea.client.iter_all_repos",
                        side_effect=GiteaError("down")):
            summary = gitea_sample_storage()
        self.account.refresh_from_db()
        self.assertEqual(self.account.storage_bytes, 5 * GB)
        self.assertFalse(summary["sampled"])

    def test_the_snapshot_row_is_updated_not_multiplied(self):
        for _ in range(2):
            with mock.patch("toto.gitea.client.iter_all_repos",
                            return_value=iter([_repo("alice", "n", 1)])):
                gitea_sample_storage()
        self.assertEqual(GiteaForgeSample.objects.count(), 1)


@override_settings(**FORGE, GITEA_STORAGE_CAP_GB=1)
class ReconcilerTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.account = GiteaAccount.objects.create(user=self.alice,
                                                   username="alice")

    def _run(self, kib, flip=None):
        with mock.patch("toto.gitea.client.iter_all_repos",
                        return_value=iter([_repo("alice", "big", kib)])), \
             mock.patch("toto.gitea.client.set_max_repo_creation",
                        side_effect=flip) as set_max:
            gitea_sample_storage()
        self.account.refresh_from_db()
        return set_max

    def test_over_the_cap_blocks_creation_exactly_once(self):
        set_max = self._run(2 * GB // 1024)
        set_max.assert_called_once_with("alice", 0)
        self.assertTrue(self.account.repo_creation_blocked)
        # Same holdings tomorrow: the forge already knows, nothing is called.
        set_max = self._run(2 * GB // 1024)
        set_max.assert_not_called()
        self.assertTrue(self.account.repo_creation_blocked)

    def test_dropping_back_under_restores_the_default(self):
        self._run(2 * GB // 1024)
        set_max = self._run(100)
        set_max.assert_called_once_with("alice", -1)
        self.assertFalse(self.account.repo_creation_blocked)

    def test_the_account_override_beats_the_host_cap(self):
        self.account.storage_cap_gb = 10
        self.account.save(update_fields=["storage_cap_gb"])
        set_max = self._run(2 * GB // 1024)   # over host 1, under own 10
        set_max.assert_not_called()
        self.assertFalse(self.account.repo_creation_blocked)

    @override_settings(GITEA_STORAGE_CAP_GB=None)
    def test_no_cap_anywhere_means_never_blocked(self):
        set_max = self._run(500 * GB // 1024)
        set_max.assert_not_called()
        self.assertFalse(self.account.repo_creation_blocked)

    def test_a_failed_flip_keeps_the_flag_honest(self):
        # The flag records what the FORGE was last told, not what we wished:
        # a failed call leaves it False so tomorrow's run retries the flip.
        set_max = self._run(2 * GB // 1024, flip=GiteaError("down"))
        set_max.assert_called_once()
        self.assertFalse(self.account.repo_creation_blocked)
        self.assertEqual(self.account.storage_bytes, 2 * GB)  # sample landed


class ProviderTests(TestCase):
    def setUp(self):
        self.provider = GiteaStorageLevy()
        self.alice = User.objects.create_user("alice", password="pw")

    def test_sample_and_measure_agree(self):
        GiteaAccount.objects.create(user=self.alice, username="alice",
                                    storage_bytes=3 * GB)
        sampled = dict(self.provider.sample())
        self.assertEqual(sampled, {self.alice.pk: 3 * GB})
        self.assertEqual(self.provider.measure(self.alice), 3 * GB)

    def test_empty_holdings_are_not_in_the_sample(self):
        GiteaAccount.objects.create(user=self.alice, username="alice",
                                    storage_bytes=0)
        self.assertEqual(list(self.provider.sample()), [])
        self.assertEqual(self.provider.measure(self.alice), 0)

    def test_no_account_measures_zero(self):
        self.assertEqual(self.provider.measure(self.alice), 0)

    def test_the_contract_constants(self):
        self.assertEqual(self.provider.metric_code, "gitea.gb_day")
        self.assertEqual(self.provider.raw_per_unit, GB)
        self.assertIn("nothing you have pushed is deleted",
                      self.provider.consequence_text)


@skipUnless(ECONOMY, "toto.tax is not installed on this host")
@override_settings(
    # The host under test is not the monetary master; the ledger fixture
    # needs an issuer key to mint the test asset. Same throwaway key as
    # toto.tax.testing.settings — a test constant, not a secret.
    MONETARY_ISSUER_KEY="0Zk8Yl1ZQ0mQ0m0YlZk8Yl1ZQ0mQ0m0YlZk8Yl1ZQ0k=")
class LevyEndToEndTests(_LedgerBase):
    """The real sweep over the snapshot: priced bills, unpriced is free."""

    def setUp(self):
        from toto.tax.models import TaxRule
        from toto.tax.tests.factories import make_gas_asset

        self.asset = make_gas_asset(decimals=9)
        self.rule = TaxRule.objects.create(metric_code="gitea.gb_day",
                                           unit_label="GB", active=True)
        self.alice = User.objects.create_user("alice", password="pw")
        GiteaAccount.objects.create(user=self.alice, username="alice",
                                    storage_bytes=3 * GB)

    def test_a_priced_day_charges_three_units(self):
        from decimal import Decimal

        from toto.quota.metrics import registry
        from toto.tariffs.models import UsageRecord
        from toto.tariffs.rate_card import upsert_price
        from toto.tax import services
        from toto.tax.tests.factories import fund_prepaid

        from toto.gitea.models import GiteaUsageEvent

        upsert_price(registry.get("gitea.gb_day"), Decimal("0.5"),
                     asset=self.asset)
        fund_prepaid(self.alice, self.asset, 10 ** 12)

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.LEVIED: 1})
        self.assertEqual(GiteaUsageEvent.objects.get().quantity, 3)
        self.assertEqual(UsageRecord.objects.count(), 1)

    def test_an_unpriced_day_charges_nothing(self):
        from toto.tariffs.models import UsageRecord
        from toto.tax import services

        services.levy_rule(self.rule)

        self.assertEqual(UsageRecord.objects.count(), 0)


# ``authenticated``: the storage line is for every forge user, and the
# members-vs-staff split under test is the unattributed number, not the door.
@override_settings(**FORGE, GITEA_ACCESS="authenticated")
class IndexStorageTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("alice", "a@example.com", "pw",
                                             is_staff=True)
        self.client.force_login(self.user)
        self.account = GiteaAccount.objects.create(user=self.user,
                                                   username="alice",
                                                   storage_bytes=2 * GB)

    def _page(self):
        with mock.patch("toto.gitea.client.ensure_account",
                        return_value=self.account), \
             mock.patch("toto.gitea.client.list_repos", return_value=[]):
            return self.client.get(reverse("gitea:index"))

    def test_the_holdings_line_renders(self):
        response = self._page()
        self.assertContains(response, "Your hosted git holds")
        self.assertContains(response, "2.0")

    @override_settings(GITEA_STORAGE_CAP_GB=5)
    def test_the_cap_renders_beside_the_holdings(self):
        self.assertContains(self._page(), "of ")

    def test_the_block_banner_shows_when_and_only_when_blocked(self):
        self.assertNotContains(self._page(), "Over the storage cap")
        self.account.repo_creation_blocked = True
        self.account.save(update_fields=["repo_creation_blocked"])
        response = self._page()
        self.assertContains(response, "Over the storage cap")
        self.assertContains(response, "nothing you have pushed is deleted")

    def test_staff_see_the_unattributed_number(self):
        from django.utils import timezone

        GiteaForgeSample.objects.create(sampled_at=timezone.now(),
                                        total_bytes=10 * GB,
                                        unattributed_bytes=4 * GB)
        self.assertContains(self._page(), "unattributed")

    def test_members_do_not(self):
        from django.utils import timezone

        GiteaForgeSample.objects.create(sampled_at=timezone.now(),
                                        total_bytes=10 * GB,
                                        unattributed_bytes=4 * GB)
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        self.assertNotContains(self._page(), "unattributed")
