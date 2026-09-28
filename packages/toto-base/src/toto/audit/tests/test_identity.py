"""Identity events on the chain (2026-09-28): sign-in, sign-out, a refused
sign-in, accounts made and their flags changed — one record each, the right
actor and outcome, and never a password."""

import json
from unittest import mock

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from toto.audit.models import AuditRecord

User = get_user_model()


def _request():
    request = RequestFactory().post("/sso/login/", REMOTE_ADDR="203.0.113.7")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = AnonymousUser()
    return request


class IdentityTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="s3cret-pw")

    def records(self, action):
        return AuditRecord.objects.filter(action=action)

    def test_an_account_made_is_one_record(self):
        record = self.records("AUTH.ACCOUNT_CREATED").get()
        self.assertEqual(record.object_id, str(self.ada.pk))
        self.assertEqual(record.object_description, "ada")
        self.assertEqual(record.app_label, "auth")
        self.assertTrue(record.metadata["is_active"])

    def test_a_sign_in_and_a_sign_out(self):
        request = _request()
        login(request, self.ada, backend="django.contrib.auth.backends.ModelBackend")
        record = self.records("AUTH.LOGIN").get()
        self.assertEqual(record.actor_user, self.ada)
        self.assertTrue(record.success)
        logout(request)
        self.assertEqual(self.records("AUTH.LOGOUT").get().actor_user, self.ada)

    def test_a_refused_sign_in_names_the_username_and_never_the_password(self):
        self.assertIsNone(authenticate(_request(), username="ada", password="wrong-guess"))
        record = self.records("AUTH.LOGIN_FAILED").get()
        self.assertFalse(record.success)
        self.assertEqual(record.metadata["username"], "ada")
        self.assertIsNone(record.actor_user)
        dump = json.dumps([record.metadata, record.changes, record.object_description])
        self.assertNotIn("wrong-guess", dump)
        self.assertNotIn("s3cret-pw", dump)

    def test_the_flags_that_matter_are_each_one_record(self):
        self.ada.is_active = False
        self.ada.save()
        self.ada.is_staff = True
        self.ada.save()
        self.ada.is_superuser = True
        self.ada.save()
        self.assertEqual(self.records("AUTH.ACCOUNT_DEACTIVATED").count(), 1)
        self.assertEqual(self.records("AUTH.STAFF_GRANTED").count(), 1)
        self.assertEqual(self.records("AUTH.SUPERUSER_GRANTED").count(), 1)
        self.ada.is_active = True
        self.ada.save(update_fields=["is_active"])
        self.assertEqual(self.records("AUTH.ACCOUNT_ACTIVATED").count(), 1)

    def test_a_save_that_changes_no_flag_records_nothing(self):
        before = AuditRecord.objects.count()
        self.ada.first_name = "Ada"
        self.ada.save()
        self.ada.save(update_fields=["last_login"])
        self.assertEqual(AuditRecord.objects.count(), before)

    def test_a_chain_that_cannot_write_never_breaks_a_signup_or_a_login(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("chain down")):
            bob = User.objects.create_user("bob", password="pw-for-bob")
            request = _request()
            login(request, bob, backend="django.contrib.auth.backends.ModelBackend")
        self.assertTrue(User.objects.filter(username="bob").exists())
        self.assertEqual(request.user, bob)
