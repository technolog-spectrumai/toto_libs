"""The two server-side strongboxes nobody types a password into: the
parameterised ``Strongbox`` (forum rooms ride it) and ``toto.gervazy.vault``
(the API connectors' secrets). Every failure must come back as the one typed
``VaultUnavailable``, the passphrase must survive rotation, and the audit
trail must never carry a value.
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

from cryptography.exceptions import InvalidTag
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings

from toto.gervazy import vault as sabbia
from toto.gervazy.models import CryptoAuditLog, EncryptedSecret, UserStrongbox, WrappedDataKey
from toto.gervazy.strongbox import Strongbox, VaultUnavailable

User = get_user_model()

SETTING = "TEST_MORE_BOX_PASSWORD"


def make_box():
    return Strongbox(name="test-more-box", owner_username="test-more-vault",
                     password_setting=SETTING, label="Testbox")


@override_settings(TEST_MORE_BOX_PASSWORD="first passphrase")
class StrongboxTests(TestCase):
    def setUp(self):
        self.box = make_box()
        self.addCleanup(self.box.clear_cache)

    def test_a_missing_or_blank_passphrase_is_unavailable_and_named(self):
        for value in ("", "   ", None):
            with self.settings(TEST_MORE_BOX_PASSWORD=value):
                with self.assertRaisesMessage(VaultUnavailable, SETTING):
                    self.box.load_password()

    def test_the_passphrase_is_read_stripped(self):
        with self.settings(TEST_MORE_BOX_PASSWORD="  padded  "):
            self.assertEqual(self.box.load_password(), "padded")

    def test_ensure_makes_the_box_once_for_a_service_account_that_cannot_log_in(self):
        first = self.box.ensure()
        second = self.box.ensure()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(UserStrongbox.objects.filter(name="test-more-box").count(), 1)
        owner = User.objects.get(username="test-more-vault")
        self.assertFalse(owner.is_active)
        self.assertFalse(owner.has_usable_password())
        self.assertEqual(first.owner, owner)

    def test_no_passphrase_means_no_box(self):
        with self.settings(TEST_MORE_BOX_PASSWORD=""):
            with self.assertRaises(VaultUnavailable):
                self.box.ensure()
        self.assertFalse(UserStrongbox.objects.filter(name="test-more-box").exists())

    def test_the_read_only_path_never_creates_a_box(self):
        with self.assertRaisesMessage(VaultUnavailable, "does not exist yet"):
            self.box.open_session(create=False)
        self.assertFalse(self.box.is_available())
        self.assertFalse(UserStrongbox.objects.filter(name="test-more-box").exists())

    def test_availability_never_raises(self):
        with self.settings(TEST_MORE_BOX_PASSWORD=""):
            self.assertFalse(self.box.is_available())
        self.box.ensure()
        self.assertTrue(self.box.is_available())

    def test_the_unlocked_session_is_reused(self):
        self.assertIs(self.box.open_session(), self.box.open_session())

    def test_a_secret_round_trips_under_the_boxs_one_data_key(self):
        a = self.box.store_secret("alpha", name="a", purpose="p")
        b = self.box.store_secret("beta", name="b")
        self.assertEqual(a.wrapped_key_id, b.wrapped_key_id)
        self.assertEqual(self.box.read_secret(a), "alpha")
        self.assertEqual(self.box.read_secret(b), "beta")

    def test_a_box_with_no_live_data_key_mints_one(self):
        self.box.ensure()
        WrappedDataKey.objects.filter(strongbox__name="test-more-box").update(state="retired")
        secret = self.box.store_secret("x", name="fresh")
        self.assertEqual(secret.wrapped_key.state, "active")
        self.assertEqual(self.box.read_secret(secret), "x")

    def test_no_secret_is_unavailable_not_a_crash(self):
        with self.assertRaisesMessage(VaultUnavailable, "No secret is stored."):
            self.box.read_secret(None)

    def test_a_changed_passphrase_is_unavailable_naming_every_cause(self):
        secret = self.box.store_secret("x", name="s")
        self.box.clear_cache()
        with self.settings(TEST_MORE_BOX_PASSWORD="someone changed it"):
            with self.assertRaises(VaultUnavailable) as caught:
                self.box.read_secret(secret)
        self.assertIn(SETTING, str(caught.exception))
        self.assertIn("InvalidTag", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, InvalidTag)

    def test_reencrypting_mints_a_new_data_key_and_keeps_value_and_purpose(self):
        old = self.box.store_secret("value", name="old", purpose="api")
        new = self.box.reencrypt_secret(old, name=self.box.unique_secret_name("rot"))
        self.assertNotEqual(new.wrapped_key_id, old.wrapped_key_id)
        self.assertEqual(new.purpose, "api")
        self.assertEqual(self.box.read_secret(new), "value")

    def test_retiring_the_last_secret_on_a_key_retires_the_key(self):
        a = self.box.store_secret("a", name="a")
        b = self.box.store_secret("b", name="b")
        self.box.retire_secret(a)
        self.assertEqual(EncryptedSecret.objects.get(pk=a.pk).state, "retired")
        self.assertEqual(WrappedDataKey.objects.get(pk=a.wrapped_key_id).state, "active")
        self.box.retire_secret(EncryptedSecret.objects.get(pk=b.pk))
        self.assertEqual(WrappedDataKey.objects.get(pk=a.wrapped_key_id).state, "retired")
        self.box.retire_secret(None)                          # a no-op, not an error

    def test_unique_names_carry_the_prefix_and_never_repeat(self):
        names = {self.box.unique_secret_name("forum") for _ in range(10)}
        self.assertEqual(len(names), 10)
        self.assertTrue(all(n.startswith("forum-") for n in names))


@override_settings(TEST_MORE_BOX_PASSWORD="first passphrase")
class RotationTests(TestCase):
    def setUp(self):
        self.box = make_box()
        self.addCleanup(self.box.clear_cache)

    def test_an_empty_new_passphrase_or_a_missing_box_is_refused(self):
        with self.assertRaises(VaultUnavailable):
            self.box.rotate_passphrase("first passphrase", "")
        with self.assertRaisesMessage(VaultUnavailable, "has not been set up yet"):
            self.box.rotate_passphrase("first passphrase", "second")

    def test_a_wrong_old_passphrase_changes_nothing(self):
        secret = self.box.store_secret("x", name="s")
        with self.assertRaises(ValueError):
            self.box.rotate_passphrase("not it", "second")
        self.box.clear_cache()
        self.assertEqual(self.box.read_secret(secret), "x")

    def test_a_secret_fetched_before_the_rotation_still_reads_after_it(self):
        stale = self.box.store_secret("still here", name="s")
        stale = EncryptedSecret.objects.select_related("wrapped_key__vmk").get(pk=stale.pk)
        self.box.rotate_passphrase("first passphrase", "second", actor=AnonymousUser())
        with self.settings(TEST_MORE_BOX_PASSWORD="second"):
            self.assertEqual(self.box.read_secret(stale), "still here")
        self.box.clear_cache()
        with self.assertRaises(VaultUnavailable):
            self.box.read_secret(stale)                      # the old one no longer opens it
        entry = CryptoAuditLog.objects.get(action="rotate_vault_passphrase")
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.object_type, "")

    def test_an_audit_reason_that_looks_like_a_value_is_dropped_not_raised(self):
        self.box.log_secret_event(None, "x", None, reason="password=hunter2")
        self.assertFalse(CryptoAuditLog.objects.exists())


class SabbiaVaultTests(TestCase):
    """``toto.gervazy.vault`` — one module-level system strongbox."""

    def setUp(self):
        sabbia.clear_cache()
        self.addCleanup(sabbia.clear_cache)
        self.run_dir = tempfile.mkdtemp(prefix="gervazy-run-")

    def boot(self, password="sabbia pw"):
        owner = User.objects.create_user(sabbia.SYSTEM_OWNER_USERNAME)
        from toto.gervazy.crypto import GervazyCryptoSession

        GervazyCryptoSession.initialize_strongbox(owner, sabbia.SYSTEM_STRONGBOX_NAME, password)

    def test_the_setting_wins_and_is_stripped(self):
        with self.settings(SABBIA_VAULT_PASSWORD="  from settings  ", TOTO_RUN_DIR=self.run_dir):
            self.assertEqual(sabbia.load_vault_password(), "from settings")

    def test_a_run_bundle_is_the_dev_fallback(self):
        Path(self.run_dir, "sabbia_dev.json").write_text(json.dumps({"vault_password": "bundled"}))
        Path(self.run_dir, "sabbia_broken.json").write_text("{not json")
        with self.settings(SABBIA_VAULT_PASSWORD="", TOTO_RUN_DIR=self.run_dir):
            self.assertEqual(sabbia.load_vault_password(), "bundled")

    def test_nothing_anywhere_is_unavailable_with_the_fix_named(self):
        with self.settings(SABBIA_VAULT_PASSWORD="", TOTO_RUN_DIR=self.run_dir), \
                mock.patch.object(sabbia, "_has_sabbia", lambda: True):
            with self.assertRaisesMessage(sabbia.VaultUnavailable,
                                          "SABBIA_VAULT_PASSWORD is not set"):
                sabbia.load_vault_password()

    def test_no_system_box_is_unavailable(self):
        with self.settings(SABBIA_VAULT_PASSWORD="x", TOTO_RUN_DIR=self.run_dir), \
                mock.patch.object(sabbia, "_has_sabbia", lambda: True):
            with self.assertRaisesMessage(sabbia.VaultUnavailable, "not initialized"):
                sabbia.open_session()
            self.assertFalse(sabbia.is_available())

    def test_a_host_without_the_agent_backend_is_not_sent_to_its_command(self):
        """The remedy is ``manage.py sabbia_init_vault``, a command of
        toto.sabbia (toto-ai). A host without that app is told what is true
        there, and never the name of an app it does not have (2026-10-06)."""
        with self.settings(SABBIA_VAULT_PASSWORD="", TOTO_RUN_DIR=self.run_dir), \
                mock.patch.object(sabbia, "_has_sabbia", lambda: False):
            for call in (sabbia.load_vault_password, sabbia.open_session):
                with self.subTest(call=call.__name__):
                    with self.assertRaises(sabbia.VaultUnavailable) as caught:
                        call()
                    sentence = str(caught.exception)
                    self.assertEqual(sentence, sabbia.NO_VAULT_HERE)
                    for word in ("sabbia", "Sabbia", "SABBIA", "manage.py"):
                        self.assertNotIn(word, sentence)
            self.assertFalse(sabbia.is_available())

    def test_store_rotate_and_retire_a_connector_secret(self):
        self.boot()
        with self.settings(SABBIA_VAULT_PASSWORD="sabbia pw", TOTO_RUN_DIR=self.run_dir):
            self.assertTrue(sabbia.is_available())
            secret = sabbia.store_secret("sk-123", name="openai", purpose="api")
            session = sabbia.open_session()
            self.assertEqual(session.decrypt_secret(secret), "sk-123")
            rotated = sabbia.reencrypt_secret(secret, name="openai-2")
            self.assertNotEqual(rotated.wrapped_key_id, secret.wrapped_key_id)
            self.assertEqual(session.decrypt_secret(rotated), "sk-123")
            sabbia.retire_secret(secret)
            self.assertEqual(WrappedDataKey.objects.get(pk=secret.wrapped_key_id).state,
                             "retired")
            sabbia.retire_secret(None)

    def test_a_box_whose_keys_were_all_retired_mints_a_new_one(self):
        self.boot()
        WrappedDataKey.objects.update(state="retired")
        with self.settings(SABBIA_VAULT_PASSWORD="sabbia pw", TOTO_RUN_DIR=self.run_dir):
            secret = sabbia.store_secret("v", name="n")
        self.assertEqual(secret.wrapped_key.state, "active")

    def test_the_audit_entry_names_the_box_and_never_an_anonymous_actor(self):
        self.boot()
        sabbia.log_secret_event(AnonymousUser(), "set_connector_secret", None, reason="admin")
        entry = CryptoAuditLog.objects.get()
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.strongbox, sabbia.system_strongbox())
        sabbia.log_secret_event(None, "x", None, reason="token=abc")   # swallowed
        self.assertEqual(CryptoAuditLog.objects.count(), 1)
