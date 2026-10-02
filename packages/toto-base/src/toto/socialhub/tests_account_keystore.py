"""Your account, the key store section (2026-10-01; on the own profile's
Security tab since 2026-10-02, stage 50): a member creates their own
personal key store once, under a passphrase they choose; a second try, a bare
box the desktop made, and a box whose salt sealed files depend on all refuse
and change nothing; nobody can make one for somebody else; neither trail
carries the passphrase.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.gervazy.crypto import GervazyCryptoSession
from toto.gervazy.models import CryptoAuditLog, UserStrongbox, VaultMasterKey, WrappedDataKey
from toto.gervazy.personal import (
    PERSONAL_STRONGBOX_NAME,
    KeyStoreExists,
    KeyStoreRefused,
    create_personal_strongbox,
    personal_strongbox,
)

User = get_user_model()
PASSPHRASE = "blue-heron-lantern"
OTHER_PASSPHRASE = "red-fox-candle"


class KeyStoreTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", "ada@example.test", "Correct-horse-9")
        self.client.force_login(self.user)
        # No profile here: the own page is drawn at /account/ itself.
        self.security = reverse("account:home") + "?tab=security"

    def page(self):
        return self.client.get(self.security, follow=True)

    def create(self, passphrase=PASSPHRASE, again=None, **extra):
        return self.client.post(reverse("account:key_store"), {
            "passphrase": passphrase,
            "passphrase2": passphrase if again is None else again, **extra})

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def records(self):
        return AuditRecord.objects.filter(action="AUTH.KEY_STORE_CREATED")

    def box(self, user=None):
        return personal_strongbox(user or self.user)

    def opens(self, box, passphrase):
        """Does the passphrase unwrap the box's master key?"""
        vmk = VaultMasterKey.objects.get(strongbox=box, state="active")
        try:
            GervazyCryptoSession(box, passphrase)._unwrap_vmk(vmk)
            return True
        except Exception:  # noqa: BLE001
            return False


class CreateTests(KeyStoreTestCase):
    def test_the_page_offers_the_form_when_there_is_none(self):
        response = self.page()
        self.assertContains(response, 'id="keystore"')
        self.assertContains(response, reverse("account:key_store"))
        self.assertIsNone(response.context["key_store"]["box"])

    def test_a_member_creates_their_key_store_once(self):
        response = self.create()
        self.assertRedirects(response, self.security + "#keystore",
                             fetch_redirect_response=False)
        box = self.box()
        self.assertEqual(box.name, PERSONAL_STRONGBOX_NAME)
        self.assertEqual(box.owner, self.user)
        self.assertEqual(VaultMasterKey.objects.filter(strongbox=box, state="active").count(), 1)
        self.assertEqual(WrappedDataKey.objects.filter(strongbox=box, state="active").count(), 1)
        self.assertTrue(self.opens(box, PASSPHRASE))
        self.assertFalse(self.opens(box, OTHER_PASSPHRASE))
        self.assertIn("Your key store is ready", " ".join(self.messages(response)))
        page = self.page()
        self.assertTrue(page.context["key_store"]["keyed"])
        self.assertNotContains(page, reverse("account:key_store"))

    def test_a_second_attempt_is_refused_and_changes_nothing(self):
        self.create()
        box = self.box()
        salt, vmk = bytes(box.salt), bytes(VaultMasterKey.objects.get(strongbox=box).encrypted_vmk)
        response = self.create(OTHER_PASSPHRASE)
        self.assertIn("already have a key store", " ".join(self.messages(response)))
        self.assertEqual(UserStrongbox.objects.filter(owner=self.user).count(), 1)
        box.refresh_from_db()
        self.assertEqual(bytes(box.salt), salt)
        self.assertEqual(VaultMasterKey.objects.filter(strongbox=box).count(), 1)
        self.assertEqual(bytes(VaultMasterKey.objects.get(strongbox=box).encrypted_vmk), vmk)
        self.assertTrue(self.opens(box, PASSPHRASE))
        self.assertEqual(self.records().count(), 1)

    def test_a_bare_box_the_desktop_made_is_not_overwritten(self):
        bare = UserStrongbox.objects.create(owner=self.user, name=PERSONAL_STRONGBOX_NAME)
        salt = bytes(bare.salt)
        self.assertContains(self.page(), "has no keys yet")
        self.create()
        bare.refresh_from_db()
        self.assertEqual(bytes(bare.salt), salt)
        self.assertFalse(VaultMasterKey.objects.filter(strongbox=bare).exists())
        self.assertFalse(self.records().exists())
        with self.assertRaises(KeyStoreExists):
            create_personal_strongbox(self.user, PASSPHRASE)

    def test_a_purpose_box_earlier_by_name_does_not_block_it(self):
        mail = UserStrongbox.objects.create(owner=self.user, name="mail")
        self.create()
        self.assertIsNotNone(self.box())
        self.assertEqual(UserStrongbox.objects.filter(owner=self.user).first(), mail)

    def test_it_never_takes_over_the_salt_sealed_files_use(self):
        # "vault-storage" sorts after "strongbox": the new box would become
        # user_strongboxes.first(), the salt every sealed vault file uses.
        storage = UserStrongbox.objects.create(owner=self.user, name="vault-storage")
        response = self.create()
        self.assertIn("sealed files depend on", " ".join(self.messages(response)))
        self.assertIsNone(self.box())
        self.assertEqual(list(UserStrongbox.objects.filter(owner=self.user)), [storage])
        self.assertFalse(VaultMasterKey.objects.exists())
        self.assertFalse(self.records().exists())
        with self.assertRaises(KeyStoreRefused):
            create_personal_strongbox(self.user, PASSPHRASE)

    def test_a_short_or_mismatched_passphrase_creates_nothing(self):
        short = self.create("five5")
        self.assertEqual(short.status_code, 400)
        self.assertIn("passphrase", short.context["key_store"]["form"].errors)
        mismatched = self.create(PASSPHRASE, again=OTHER_PASSPHRASE)
        self.assertEqual(mismatched.status_code, 400)
        self.assertIn("passphrase2", mismatched.context["key_store"]["form"].errors)
        self.assertNotContains(mismatched, PASSPHRASE, status_code=400)
        self.assertIsNone(self.box())
        with self.assertRaises(ValueError):
            create_personal_strongbox(self.user, "five5")

    def test_the_keys_page_sends_a_member_without_one_here(self):
        response = self.client.get(reverse("gervazy:my_keys"))
        self.assertContains(response, self.security + "#keystore")

    def test_only_a_signed_in_post_creates(self):
        self.assertEqual(self.client.get(reverse("account:key_store")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.create().status_code, 302)
        self.assertIsNone(self.box())


class OwnAccountOnlyTests(KeyStoreTestCase):
    def test_another_member_cannot_create_one_for_somebody_else(self):
        other = User.objects.create_user("mallory", "m@example.test", "Correct-horse-9")
        self.client.force_login(other)
        self.create(owner=self.user.pk, user=self.user.pk, user_id=self.user.pk,
                    username="ada")
        self.assertIsNone(self.box())
        self.assertEqual(self.box(other).owner, other)
        self.assertEqual(self.records().get().actor_user, other)

    def test_the_door_takes_no_account_in_its_address(self):
        self.assertEqual(reverse("account:key_store"), "/account/key-store/")


class NoSecretTests(KeyStoreTestCase):
    def test_the_chain_names_the_box_and_nothing_secret(self):
        self.create()
        box = self.box()
        record = self.records().get()
        self.assertEqual(record.actor_user, self.user)
        self.assertEqual(record.object_id, str(self.user.pk))
        self.assertEqual(record.metadata, {"strongbox_id": box.pk})
        self.assertEqual(record.request_source["path"], "/account/key-store/")
        blob = json.dumps([record.metadata, record.changes, record.request_source,
                           record.object_description])
        self.assertNotIn(PASSPHRASE, blob)

    def test_gervazys_trail_names_the_box_and_nothing_secret(self):
        self.create()
        entry = CryptoAuditLog.objects.get(action="create_personal_strongbox")
        self.assertEqual(entry.strongbox, self.box())
        self.assertEqual(entry.actor, self.user)
        self.assertNotIn(PASSPHRASE, f"{entry.reason} {entry.user_agent} {entry.object_id}")

    def test_the_passphrase_is_not_kept_in_the_session(self):
        self.create()
        session = self.client.session
        self.assertNotIn(PASSPHRASE, json.dumps(
            {key: str(session[key]) for key in session.keys()}))

    def test_recent_sign_ins_label_the_creation(self):
        self.create()
        page = self.page()
        labels = [row["label"] for row in page.context["signins_page"].rows]
        self.assertIn("Key store created", [str(label) for label in labels])
