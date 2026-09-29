"""The Gervazy envelope past the blob helpers tests.py pins: the AES-GCM
argument checks, which key states a session refuses, secrets and private
keys bound to their rows, chunked files, and passphrase rotation that must
change nothing below the master key — and nothing at all when it fails.
"""

import base64
import os
import tempfile
from datetime import timedelta

from cryptography.exceptions import InvalidTag
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from toto.gervazy import crypto
from toto.gervazy.crypto import GervazyCryptoSession
from toto.gervazy.models import (CryptoAuditLog, EncryptedFile, EncryptedFileChunk,
                                 EncryptedSecret, UserStrongbox, VaultMasterKey,
                                 WrappedDataKey)

User = get_user_model()

KEY = b"k" * 32


class AesHelperTests(SimpleTestCase):
    def test_round_trip_with_and_without_aad(self):
        for aad in (b"", None, b"ctx"):
            ciphertext, nonce = crypto.aes_gcm_encrypt(KEY, b"plain", aad)
            self.assertEqual(len(nonce), crypto.AES_GCM_NONCE_SIZE)
            self.assertEqual(crypto.aes_gcm_decrypt(KEY, ciphertext, nonce, aad), b"plain")

    def test_every_encryption_gets_a_fresh_nonce(self):
        nonces = {crypto.aes_gcm_encrypt(KEY, b"same")[1] for _ in range(20)}
        self.assertEqual(len(nonces), 20)

    def test_the_key_must_be_32_bytes_of_bytes(self):
        with self.assertRaises(ValueError):
            crypto.aes_gcm_encrypt(b"short", b"x")
        with self.assertRaises(TypeError):
            crypto.aes_gcm_encrypt("k" * 32, b"x")
        with self.assertRaises(ValueError):
            crypto.aes_gcm_decrypt(b"k" * 16, b"x" * 20, b"n" * 12)

    def test_the_nonce_must_be_12_bytes_of_bytes(self):
        ciphertext, _ = crypto.aes_gcm_encrypt(KEY, b"x")
        with self.assertRaises(ValueError):
            crypto.aes_gcm_decrypt(KEY, ciphertext, b"n" * 16)
        with self.assertRaises(TypeError):
            crypto.aes_gcm_decrypt(KEY, ciphertext, "n" * 12)

    def test_text_is_refused_where_bytes_are_owed(self):
        with self.assertRaises(TypeError):
            crypto.aes_gcm_encrypt(KEY, "plain")
        with self.assertRaises(TypeError):
            crypto.aes_gcm_encrypt(KEY, b"plain", "aad")
        ciphertext, nonce = crypto.aes_gcm_encrypt(KEY, b"x")
        with self.assertRaises(TypeError):
            crypto.aes_gcm_decrypt(KEY, bytearray(ciphertext), nonce)
        with self.assertRaises(TypeError):
            crypto.aes_gcm_decrypt(KEY, ciphertext, nonce, "aad")

    def test_a_different_aad_or_key_fails_authentication(self):
        ciphertext, nonce = crypto.aes_gcm_encrypt(KEY, b"x", b"one")
        with self.assertRaises(InvalidTag):
            crypto.aes_gcm_decrypt(KEY, ciphertext, nonce, b"two")
        with self.assertRaises(InvalidTag):
            crypto.aes_gcm_decrypt(b"j" * 32, ciphertext, nonce, b"one")

    def test_a_derived_key_must_decode_to_32_bytes(self):
        self.assertEqual(crypto.decode_derived_key(base64.urlsafe_b64encode(KEY)), KEY)
        with self.assertRaises(ValueError):
            crypto.decode_derived_key(base64.urlsafe_b64encode(b"k" * 16))

    def test_generated_keys_are_random_and_the_right_size(self):
        a, b = crypto.generate_raw_key(), crypto.generate_raw_key()
        self.assertEqual(len(a), 32)
        self.assertNotEqual(a, b)


class StrongboxModelTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("keeper", password="x")

    def box(self, **kwargs):
        return UserStrongbox(owner=self.owner, name=kwargs.pop("name", "b"), **kwargs)

    def test_a_nameless_box_is_named_for_you(self):
        box = UserStrongbox.objects.create(owner=self.owner, name="")
        self.assertTrue(box.name.startswith("strongbox-"))

    def test_weak_kdf_settings_are_refused(self):
        for kwargs in ({"argon2_memory_cost": 1024}, {"argon2_iterations": 1},
                       {"argon2_lanes": 0}, {"kdf": "pbkdf2"}, {"salt": b"short"}):
            with self.assertRaises(ValidationError, msg=kwargs):
                self.box(**kwargs).save()

    def test_one_name_per_owner(self):
        UserStrongbox.objects.create(owner=self.owner, name="same")
        with self.assertRaises(ValidationError):
            UserStrongbox.objects.create(owner=self.owner, name="same")

    def test_the_key_depends_on_password_and_salt_and_needs_a_password(self):
        a = UserStrongbox.objects.create(owner=self.owner, name="a", argon2_memory_cost=19456,
                                         argon2_iterations=2, argon2_lanes=1)
        b = UserStrongbox.objects.create(owner=self.owner, name="b", argon2_memory_cost=19456,
                                         argon2_iterations=2, argon2_lanes=1)
        self.assertEqual(a.derive_key("pw"), a.derive_key("pw"))
        self.assertNotEqual(a.derive_key("pw"), a.derive_key("pw2"))
        self.assertNotEqual(a.derive_key("pw"), b.derive_key("pw"))
        with self.assertRaises(ValueError):
            a.derive_key("")

    def test_the_audit_log_refuses_a_reason_that_carries_a_value(self):
        for reason in ("password=hunter2", "API TOKEN=abc", "leaked private_key"):
            with self.assertRaises(ValidationError, msg=reason):
                CryptoAuditLog.objects.create(action="x", success=True, reason=reason)
        CryptoAuditLog.objects.create(action="x", success=True, reason="rotated the key")


class _SessionFixture(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("keeper", password="x")
        self.session, self.wk = GervazyCryptoSession.initialize_strongbox(
            self.owner, "box", "pw")
        self.box = self.session._strongbox


class InitializeTests(_SessionFixture):
    def test_a_box_is_born_with_one_active_master_and_one_active_data_key(self):
        self.assertEqual(list(self.box.master_keys.values_list("version", "state")),
                         [(1, "active")])
        self.assertEqual(list(self.box.data_keys.values_list("version", "state")),
                         [(1, "active")])

    def test_an_empty_password_creates_nothing(self):
        with self.assertRaises(ValueError):
            GervazyCryptoSession.initialize_strongbox(self.owner, "other", "")
        self.assertFalse(UserStrongbox.objects.filter(name="other").exists())

    def test_the_legacy_alias_is_the_same_method(self):
        self.assertEqual(GervazyCryptoSession.initialize_vault,
                         GervazyCryptoSession.initialize_strongbox)


class KeyStateTests(_SessionFixture):
    def test_the_wrong_password_cannot_unwrap_anything(self):
        ciphertext, nonce = self.session.encrypt_blob(self.wk, b"x")
        wrong = GervazyCryptoSession(self.box, "not-pw")
        with self.assertRaises(InvalidTag):
            wrong.decrypt_blob(self.wk, ciphertext, nonce)

    def test_a_key_from_another_box_is_refused_by_name(self):
        _, foreign_wk = GervazyCryptoSession.initialize_strongbox(self.owner, "other", "pw")
        with self.assertRaisesMessage(RuntimeError, "does not belong to this strongbox"):
            self.session.encrypt_blob(foreign_wk, b"x")

    def test_a_retired_data_key_no_longer_encrypts_or_decrypts(self):
        ciphertext, nonce = self.session.encrypt_blob(self.wk, b"x")
        self.wk.state = "retired"
        self.wk.save()
        fresh = GervazyCryptoSession(self.box, "pw")
        with self.assertRaisesMessage(RuntimeError, "not active"):
            fresh.decrypt_blob(self.wk, ciphertext, nonce)
        self.assertIsNotNone(WrappedDataKey.objects.get(pk=self.wk.pk).rotated_at)

    def test_a_suspended_master_key_takes_its_data_keys_with_it(self):
        VaultMasterKey.objects.filter(strongbox=self.box).update(state="suspended")
        wk = WrappedDataKey.objects.select_related("vmk").get(pk=self.wk.pk)
        with self.assertRaisesMessage(RuntimeError, "VMK is not active"):
            GervazyCryptoSession(self.box, "pw").encrypt_blob(wk, b"x")

    def test_no_new_data_key_without_an_active_master(self):
        VaultMasterKey.objects.filter(strongbox=self.box).update(state="retired")
        with self.assertRaisesMessage(RuntimeError, "No active VMK"):
            self.session.create_data_key()

    def test_new_data_keys_count_up(self):
        second = self.session.create_data_key()
        third = self.session.create_data_key()
        self.assertEqual((second.version, third.version), (2, 3))

    def test_the_model_refuses_a_data_key_under_another_boxs_master(self):
        other_session, _ = GervazyCryptoSession.initialize_strongbox(self.owner, "other", "pw")
        foreign_vmk = other_session._strongbox.master_keys.get()
        with self.assertRaises(ValidationError):
            WrappedDataKey.objects.create(strongbox=self.box, vmk=foreign_vmk,
                                          encrypted_dek=b"x" * 48)

    def test_blob_plaintext_must_be_bytes(self):
        with self.assertRaises(TypeError):
            self.session.encrypt_blob(self.wk, "text")

    def test_closing_forgets_the_unwrapped_keys(self):
        self.session.close()
        self.assertEqual(self.session._dek_cache, {})
        self.assertEqual(self.session._vmk_cache, {})


class SecretTests(_SessionFixture):
    def test_a_secret_round_trips_and_is_stored_as_ciphertext(self):
        secret = self.session.encrypt_secret(self.wk, "hunter2", name="smtp", purpose="mail")
        row = EncryptedSecret.objects.get(pk=secret.pk)
        self.assertNotIn(b"hunter2", bytes(row.ciphertext))
        self.assertEqual(bytes(row.aad), row.build_aad())
        self.assertEqual(GervazyCryptoSession(self.box, "pw").decrypt_secret(row), "hunter2")

    def test_a_ciphertext_moved_to_another_row_does_not_open(self):
        a = self.session.encrypt_secret(self.wk, "alpha", name="a")
        b = self.session.encrypt_secret(self.wk, "beta", name="b")
        # Copied in memory: the (key, nonce) constraint already stops a stored copy.
        b.ciphertext, b.nonce = a.ciphertext, a.nonce
        with self.assertRaises(InvalidTag):
            self.session.decrypt_secret(b)

    def test_an_expired_or_retired_secret_is_refused(self):
        expired = self.session.encrypt_secret(self.wk, "x", name="exp")
        EncryptedSecret.objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaisesMessage(RuntimeError, "expired"):
            self.session.decrypt_secret(EncryptedSecret.objects.get(pk=expired.pk))
        retired = self.session.encrypt_secret(self.wk, "y", name="ret")
        retired.state = "retired"
        retired.save()
        self.assertIsNotNone(retired.rotated_at)
        self.assertFalse(retired.can_decrypt())
        with self.assertRaisesMessage(RuntimeError, "not active"):
            self.session.decrypt_secret(retired)

    def test_can_decrypt_follows_every_layer(self):
        secret = self.session.encrypt_secret(self.wk, "x", name="s")
        self.assertTrue(secret.can_decrypt())
        VaultMasterKey.objects.filter(strongbox=self.box).update(state="compromised")
        self.assertFalse(EncryptedSecret.objects.get(pk=secret.pk).can_decrypt())

    def test_two_secrets_cannot_share_a_name_in_one_box(self):
        self.session.encrypt_secret(self.wk, "x", name="dup")
        with self.assertRaises(ValidationError):
            self.session.encrypt_secret(self.wk, "y", name="dup")

    def test_a_private_key_round_trips_with_its_aad(self):
        epk = self.session.encrypt_private_key(
            self.wk, "-----PEM-----", key_id="k1", key_type="Ed25519",
            public_key_pem="pub", aad=b"ctx")
        self.assertEqual(self.session.decrypt_private_key(epk), "-----PEM-----")
        epk.aad = b"other"
        with self.assertRaises(InvalidTag):
            self.session.decrypt_private_key(epk)

    def test_a_retired_private_key_is_refused(self):
        epk = self.session.encrypt_private_key(self.wk, "pem", key_id="k2", key_type="Ed25519",
                                               public_key_pem="pub")
        epk.state = "retired"
        epk.save()
        self.assertIsNotNone(epk.retired_at)
        with self.assertRaisesMessage(RuntimeError, "not active"):
            self.session.decrypt_private_key(epk)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="gervazy-more-crypto-"))
class ChunkedFileTests(_SessionFixture):
    def test_chunks_decrypt_in_index_order_with_the_name(self):
        dek = self.session._unwrap_dek(self.wk)
        name_ct, name_nonce = crypto.aes_gcm_encrypt(dek, "report.pdf".encode())
        parts = [b"first-", b"second-", b"third"]
        sealed = [crypto.aes_gcm_encrypt(dek, part) for part in parts]
        ef = EncryptedFile(strongbox=self.box, owner=self.owner, wrapped_key=self.wk,
                           original_name_encrypted=name_ct, original_name_nonce=name_nonce,
                           chunk_count=len(parts))
        ef.file.save("blob.bin", ContentFile(b"".join(ct for ct, _ in sealed)), save=False)
        ef.save()
        # Rows created out of order; the index decides.
        for index in (2, 0, 1):
            ct, nonce = sealed[index]
            EncryptedFileChunk.objects.create(encrypted_file=ef, index=index, nonce=nonce,
                                              ciphertext_size=len(ct))
        data, filename = GervazyCryptoSession(self.box, "pw").decrypt_file(ef)
        self.assertEqual((data, filename), (b"first-second-third", "report.pdf"))

    def test_a_chunk_size_below_4k_is_refused(self):
        with self.assertRaises(ValidationError):
            EncryptedFile(strongbox=self.box, owner=self.owner, wrapped_key=self.wk,
                          original_name_encrypted=b"x", chunk_size=1024,
                          file="x.bin").full_clean()


class RewrapTests(_SessionFixture):
    def test_the_new_passphrase_opens_everything_and_nothing_below_moved(self):
        ciphertext, nonce = self.session.encrypt_blob(self.wk, b"payload")
        dek_before = bytes(WrappedDataKey.objects.get(pk=self.wk.pk).encrypted_dek)
        GervazyCryptoSession.rewrap_master_keys(self.box, "pw", "new-pw")
        box = UserStrongbox.objects.get(pk=self.box.pk)
        wk = WrappedDataKey.objects.select_related("vmk").get(pk=self.wk.pk)
        self.assertEqual(bytes(wk.encrypted_dek), dek_before)
        self.assertEqual(GervazyCryptoSession(box, "new-pw").decrypt_blob(wk, ciphertext, nonce),
                         b"payload")
        with self.assertRaises(InvalidTag):
            GervazyCryptoSession(box, "pw").decrypt_blob(wk, ciphertext, nonce)

    def test_a_wrong_current_passphrase_changes_nothing(self):
        salt = bytes(self.box.salt)
        vmk = bytes(self.box.master_keys.get().encrypted_vmk)
        with self.assertRaisesMessage(ValueError, "current passphrase is incorrect"):
            GervazyCryptoSession.rewrap_master_keys(self.box, "wrong", "new-pw")
        box = UserStrongbox.objects.get(pk=self.box.pk)
        self.assertEqual(bytes(box.salt), salt)
        self.assertEqual(bytes(box.master_keys.get().encrypted_vmk), vmk)

    def test_both_passphrases_are_required(self):
        for old, new in (("", "x"), ("pw", "")):
            with self.assertRaises(ValueError):
                GervazyCryptoSession.rewrap_master_keys(self.box, old, new)

    def test_a_box_with_no_live_master_key_cannot_be_rewrapped(self):
        VaultMasterKey.objects.filter(strongbox=self.box).update(state="destroyed")
        with self.assertRaisesMessage(RuntimeError, "no master key"):
            GervazyCryptoSession.rewrap_master_keys(self.box, "pw", "new-pw")
