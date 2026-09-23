"""The provider registry, and vault's storage provider measured for real."""

from decimal import Decimal

from toto.assets.testing import LedgerTestCase as TestCase

from toto.quota.levy import DuplicateLevyProvider, LevyProvider, LevyRegistry, registry

from .factories import GB, make_user, make_vault_file


class RegistryTests(TestCase):
    def test_register_get_and_duplicate(self):
        reg = LevyRegistry()

        class A(LevyProvider):
            code = "a"
            metric_code = "m.one"

        class B(LevyProvider):
            code = "b"
            metric_code = "m.one"

        a = A()
        reg.register(a)
        self.assertIs(reg.get("m.one"), a)
        reg.register(a)  # same object twice is fine (module re-import)
        with self.assertRaises(DuplicateLevyProvider):
            reg.register(B())
        self.assertEqual([p.metric_code for p in reg.all()], ["m.one"])

    def test_vault_provider_is_autodiscovered(self):
        provider = registry.get("storage.gb_day")
        self.assertIsNotNone(provider)
        self.assertEqual(provider.code, "vault.storage")
        self.assertEqual(provider.raw_per_unit, GB)


class StorageLevyMeasurementTests(TestCase):
    def test_sample_sums_per_owner_and_skips_the_fileless(self):
        provider = registry.get("storage.gb_day")
        alice = make_user("alice")
        bob = make_user("bob")
        make_user("carol")  # no files — must not appear
        make_vault_file(alice, 100)
        make_vault_file(alice, 150)
        make_vault_file(bob, 7)

        samples = dict(provider.sample())
        self.assertEqual(samples, {alice.pk: 250, bob.pk: 7})

    def test_measure_matches_sample_and_one_gb_is_exact(self):
        provider = registry.get("storage.gb_day")
        alice = make_user("alice")
        make_vault_file(alice, GB)

        self.assertEqual(provider.measure(alice), GB)
        self.assertEqual(Decimal(provider.measure(alice)) / provider.raw_per_unit,
                         Decimal("1"))

    def test_measure_counts_only_the_owner(self):
        provider = registry.get("storage.gb_day")
        alice = make_user("alice")
        bob = make_user("bob")
        make_vault_file(alice, 100)
        make_vault_file(bob, 900)

        self.assertEqual(provider.measure(alice), 100)


class PlaintextLevyTests(TestCase):
    """Security mana's drain: gigabytes held unencrypted, and only those."""

    def test_it_is_autodiscovered(self):
        provider = registry.get("security.plain_gb_day")
        self.assertIsNotNone(provider)
        self.assertEqual(provider.code, "vault.plaintext")
        self.assertEqual(provider.raw_per_unit, GB)

    def test_only_unencrypted_bytes_count(self):
        from toto.vault.models import VaultFile

        user = make_user("plain")
        make_vault_file(user, GB)
        sealed = make_vault_file(user, 3 * GB)
        VaultFile.objects.filter(pk=sealed.pk).update(is_encrypted=True)
        provider = registry.get("security.plain_gb_day")
        self.assertEqual(provider.measure(user), GB)
        self.assertEqual(dict(provider.sample())[user.pk], GB)
