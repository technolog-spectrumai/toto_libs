"""The SSO strongbox: where its passphrase comes from, and what it refuses.

Named tests_more_vault.py (sibling of tests_recovery.py) and meant for the
gate's host-owned block, where toto.gervazy is installed and real. Nothing is
mocked about the crypto: a secret is sealed with Argon2id + AES-GCM and read
back, and a wrong passphrase is a real AEAD failure. The runtime directory the
dev fallback reads is a scratch directory, never the machine's own run/.
"""
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from . import vault

User = get_user_model()

PASSPHRASE = "sso-vault-test-passphrase"


class PassphraseTests(TestCase):
    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp())

    def test_the_setting_wins_and_is_trimmed(self):
        with override_settings(SSO_VAULT_PASSWORD="  from-settings \n",
                               TOTO_RUN_DIR=str(self.run_dir)):
            self.assertEqual(vault.load_vault_password(), "from-settings")

    def test_a_dev_bundle_in_the_run_dir_is_the_fallback(self):
        (self.run_dir / "sso_broken.json").write_text("{not json")
        (self.run_dir / "sso_empty.json").write_text(json.dumps({"vault_password": ""}))
        (self.run_dir / "sso_zenobia.json").write_text(
            json.dumps({"vault_password": "from-bundle"}))
        with override_settings(SSO_VAULT_PASSWORD="", TOTO_RUN_DIR=str(self.run_dir)):
            self.assertEqual(vault.load_vault_password(), "from-bundle")

    def test_no_passphrase_anywhere_is_a_typed_refusal(self):
        (self.run_dir / "other.json").write_text(json.dumps({"vault_password": "no"}))
        with override_settings(SSO_VAULT_PASSWORD="", TOTO_RUN_DIR=str(self.run_dir)):
            with self.assertRaises(vault.VaultUnavailable) as ctx:
                vault.load_vault_password()
            self.assertIn("SSO_VAULT_PASSWORD", str(ctx.exception))
            self.assertFalse(vault.is_available())
        self.assertIsNone(vault.system_strongbox())


@override_settings(SSO_VAULT_PASSWORD=PASSPHRASE)
class StrongboxTests(TestCase):
    def setUp(self):
        vault.clear_cache()
        self.addCleanup(vault.clear_cache)

    def test_the_box_is_created_once_owned_by_an_account_that_cannot_log_in(self):
        first = vault.ensure_strongbox()
        second = vault.ensure_strongbox()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.name, vault.SSO_STRONGBOX_NAME)
        owner = User.objects.get(username=vault.SSO_OWNER_USERNAME)
        self.assertFalse(owner.is_active)
        self.assertFalse(owner.has_usable_password())

    def test_the_read_only_path_never_creates_a_box(self):
        with self.assertRaises(vault.VaultUnavailable) as ctx:
            vault.open_session(create=False)
        self.assertIn("does not exist yet", str(ctx.exception))
        self.assertIsNone(vault.system_strongbox())

    def test_a_secret_round_trips_and_shares_one_data_key(self):
        first = vault.store_secret("client-secret-1", name=vault.unique_secret_name())
        second = vault.store_secret("client-secret-2", name=vault.unique_secret_name())

        self.assertEqual(vault.read_secret(first), "client-secret-1")
        self.assertEqual(vault.read_secret(second, create=False), "client-secret-2")
        self.assertEqual(first.wrapped_key_id, second.wrapped_key_id)
        self.assertEqual(first.purpose, vault.PURPOSE_CLIENT_SECRET)
        self.assertTrue(vault.is_available())

    def test_no_stored_secret_is_a_refusal_not_a_crash(self):
        with self.assertRaises(vault.VaultUnavailable):
            vault.read_secret(None)

    def test_a_changed_passphrase_reads_as_a_locked_vault(self):
        secret = vault.store_secret("s", name=vault.unique_secret_name())
        vault.clear_cache()
        with override_settings(SSO_VAULT_PASSWORD="some-other-passphrase"):
            with self.assertRaises(vault.VaultUnavailable) as ctx:
                vault.read_secret(secret)
        self.assertIn("Pair again", str(ctx.exception))
        self.assertNotIn("some-other-passphrase", str(ctx.exception))

    def test_retiring_the_last_secret_retires_its_data_key_too(self):
        secret = vault.store_secret("s", name=vault.unique_secret_name())

        vault.retire_secret(secret)

        secret.refresh_from_db()
        secret.wrapped_key.refresh_from_db()
        self.assertEqual(secret.state, "retired")
        self.assertEqual(secret.wrapped_key.state, "retired")

    def test_a_data_key_another_secret_still_uses_stays_active(self):
        old = vault.store_secret("old", name=vault.unique_secret_name())
        vault.store_secret("new", name=vault.unique_secret_name())

        vault.retire_secret(old)

        old.wrapped_key.refresh_from_db()
        self.assertEqual(old.wrapped_key.state, "active")
        vault.retire_secret(None)  # nothing stored: a no-op

    def test_names_are_prefixed_and_do_not_repeat(self):
        names = {vault.unique_secret_name("oidc") for _ in range(20)}
        self.assertEqual(len(names), 20)
        self.assertTrue(all(n.startswith("oidc-") and len(n) == 17 for n in names))
