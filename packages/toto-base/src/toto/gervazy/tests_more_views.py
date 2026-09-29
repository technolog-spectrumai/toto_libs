"""The keys page (/gervazy/): initialising a bare strongbox and provisioning
a signing key — owner-only, POST-only, a wrong password changes nothing —
and the signatures those keys make.
"""

import base64
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.gervazy.crypto import GervazyCryptoSession
from toto.gervazy.models import PersonSigningKey, UserStrongbox, VaultMasterKey, WrappedDataKey
from toto.gervazy.signing import SigningError, SigningService
from toto.people.models import Person

User = get_user_model()


class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.user = User.objects.create_user("keyholder", password="pw")
        cls.other = User.objects.create_user("other", password="pw")

    def setUp(self):
        self.client.force_login(self.user)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def bare_box(self, owner=None, name="bare"):
        return UserStrongbox.objects.create(owner=owner or self.user, name=name)


class MyKeysPageTests(_Fixture):
    def test_the_page_lists_only_my_boxes_and_copes_without_a_person(self):
        self.bare_box(name="mine")
        self.bare_box(owner=self.other, name="theirs")
        response = self.client.get(reverse("gervazy:my_keys"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([b.name for b in response.context["strongboxes"]], ["mine"])
        self.assertIsNone(response.context["person"])
        self.assertEqual(response.context["signing_keys"], [])

    def test_a_logged_out_visitor_is_sent_to_log_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("gervazy:my_keys")).status_code, 302)


class InitializeTests(_Fixture):
    def url(self, box):
        return reverse("gervazy:initialize_strongbox", args=[box.pk])

    def test_only_a_post_initialises(self):
        self.assertEqual(self.client.get(self.url(self.bare_box())).status_code, 405)

    def test_someone_elses_box_is_404(self):
        box = self.bare_box(owner=self.other)
        self.assertEqual(self.client.post(self.url(box), {"password": "pw"}).status_code, 404)
        self.assertFalse(box.master_keys.exists())

    def test_a_password_is_required(self):
        box = self.bare_box()
        response = self.client.post(self.url(box), {"password": "  "})
        self.assertRedirects(response, reverse("gervazy:my_keys"), fetch_redirect_response=False)
        self.assertIn("Password is required to initialize the strongbox.",
                      self.messages(response))
        self.assertFalse(box.master_keys.exists())

    def test_a_bare_box_gets_a_master_and_a_data_key_that_work(self):
        box = self.bare_box()
        self.client.post(self.url(box), {"password": "secret"})
        self.assertEqual(list(box.master_keys.values_list("version", "state")), [(1, "active")])
        wk = WrappedDataKey.objects.select_related("vmk").get(strongbox=box)
        session = GervazyCryptoSession(UserStrongbox.objects.get(pk=box.pk), "secret")
        ciphertext, nonce = session.encrypt_blob(wk, b"hello")
        self.assertEqual(session.decrypt_blob(wk, ciphertext, nonce), b"hello")

    def test_an_initialised_box_gets_another_data_key_for_the_right_password(self):
        box = self.bare_box()
        self.client.post(self.url(box), {"password": "secret"})
        self.client.post(self.url(box), {"password": "secret"})
        self.assertEqual(sorted(box.data_keys.values_list("version", flat=True)), [1, 2])
        self.assertEqual(box.master_keys.count(), 1)

    def test_the_wrong_password_on_an_initialised_box_adds_nothing(self):
        box = self.bare_box()
        self.client.post(self.url(box), {"password": "secret"})
        response = self.client.post(self.url(box), {"password": "guess"})
        self.assertTrue(any(m.startswith("Initialization failed") for m in self.messages(response)))
        self.assertEqual(box.data_keys.count(), 1)

    def test_a_retired_master_key_is_replaced_by_a_new_version(self):
        box = self.bare_box()
        self.client.post(self.url(box), {"password": "secret"})
        VaultMasterKey.objects.filter(strongbox=box).update(state="retired")
        self.client.post(self.url(box), {"password": "anything"})
        self.assertEqual(list(box.master_keys.filter(state="active")
                              .values_list("version", flat=True)), [2])


class ProvisionTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.url = reverse("gervazy:provision_signing_key")

    def person(self):
        return Person.objects.create(user=self.user, display_name="Key Holder")

    def ready_box(self, password="secret"):
        session, wk = GervazyCryptoSession.initialize_strongbox(self.user, "signing", password)
        return session._strongbox

    def test_no_person_no_key(self):
        response = self.client.post(self.url, {"password": "secret"})
        self.assertIn("No Person profile linked to your account.", self.messages(response))

    def test_a_password_is_required(self):
        self.person()
        response = self.client.post(self.url, {"password": ""})
        self.assertIn("Password is required.", self.messages(response))

    def test_no_box_at_all_is_said_plainly(self):
        self.person()
        response = self.client.post(self.url, {"password": "secret"})
        self.assertIn("No strongbox found.", self.messages(response))

    def test_a_box_without_a_data_key_must_be_initialised_first(self):
        self.person()
        box = self.bare_box()
        response = self.client.post(self.url, {"password": "secret", "strongbox_id": box.pk})
        self.assertTrue(any("has no active data key" in m for m in self.messages(response)))

    def test_someone_elses_box_is_not_used(self):
        self.person()
        session, _ = GervazyCryptoSession.initialize_strongbox(self.other, "theirs", "secret")
        self.client.post(self.url, {"password": "secret",
                                    "strongbox_id": session._strongbox.pk})
        self.assertFalse(PersonSigningKey.objects.exists())

    def test_a_new_key_retires_the_old_one(self):
        person = self.person()
        self.ready_box()
        self.client.post(self.url, {"password": "secret"})
        self.client.post(self.url, {"password": "secret"})
        keys = PersonSigningKey.objects.filter(person=person)
        self.assertEqual(keys.count(), 2)
        self.assertEqual(keys.filter(is_active=True).count(), 1)

    def test_the_wrong_password_provisions_nothing(self):
        self.person()
        self.ready_box()
        response = self.client.post(self.url, {"password": "guess"})
        self.assertTrue(any(m.startswith("Provisioning failed") for m in self.messages(response)))
        self.assertFalse(PersonSigningKey.objects.exists())


class SigningTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.person = Person.objects.create(user=self.user, display_name="Signer")
        self.session, self.wk = GervazyCryptoSession.initialize_strongbox(
            self.user, "signing", "secret")

    def test_no_key_and_no_data_key_to_make_one_is_refused(self):
        with self.assertRaises(SigningError):
            SigningService.sign_document(self.session, self.person, b"payload")

    def test_a_signature_verifies_and_a_changed_payload_does_not(self):
        signed = SigningService.sign_document(self.session, self.person, b"I agree",
                                              wrapped_key=self.wk)
        self.assertTrue(SigningService.verify(self.person, b"I agree", signed.signature_b64))
        self.assertFalse(SigningService.verify(self.person, b"I agree!", signed.signature_b64))
        self.assertTrue(SigningService.verify_with_public_key(
            signed.public_key_pem, b"I agree", signed.signature_b64))
        self.assertFalse(SigningService.verify_with_public_key(
            signed.public_key_pem, b"other", signed.signature_b64))
        self.assertFalse(SigningService.verify_with_public_key("not a pem", b"I agree",
                                                               signed.signature_b64))

    def test_an_old_signature_still_verifies_after_the_key_is_replaced(self):
        old = SigningService.sign_document(self.session, self.person, b"v1", wrapped_key=self.wk)
        SigningService.provision_signing_key(self.session, self.wk, self.person)
        new = SigningService.sign_document(self.session, self.person, b"v2")
        self.assertNotEqual(old.signing_key_id, new.signing_key_id)
        self.assertTrue(SigningService.verify(self.person, b"v1", old.signature_b64))

    def test_a_session_on_another_box_cannot_sign_with_this_key(self):
        SigningService.provision_signing_key(self.session, self.wk, self.person)
        other_session, _ = GervazyCryptoSession.initialize_strongbox(self.user, "other", "x")
        with self.assertRaisesMessage(SigningError, "does not match"):
            SigningService.sign_document(other_session, self.person, b"payload")

    def test_a_signature_is_not_a_signature_for_someone_else(self):
        signed = SigningService.sign_document(self.session, self.person, b"x",
                                              wrapped_key=self.wk)
        stranger = Person.objects.create(user=self.other, display_name="Stranger")
        self.assertFalse(SigningService.verify(stranger, b"x", signed.signature_b64))

    def test_the_contract_file_payload_is_exactly_the_documented_lines(self):
        payload = SigningService.canonical_contract_file_payload(
            "doc-1", 3, "abc", "party-9", "2026-09-29T10:00:00+00:00")
        self.assertEqual(payload, b"sign:contract-file\ndoc:doc-1\nversion:3\n"
                                  b"content-sha256:abc\nparty:party-9\n"
                                  b"at:2026-09-29T10:00:00+00:00")

    def test_a_signed_contract_file_payload_verifies_from_the_file_alone(self):
        at = datetime(2026, 9, 29, 10, tzinfo=dt_timezone.utc).isoformat()
        payload = SigningService.canonical_contract_file_payload("d", 1, "h", "p", at)
        signed = SigningService.sign_document(self.session, self.person, payload,
                                              wrapped_key=self.wk)
        self.assertEqual(len(base64.b64decode(signed.signature_b64)), 64)   # Ed25519
        self.assertTrue(SigningService.verify_with_public_key(
            signed.public_key_pem, payload, signed.signature_b64))
