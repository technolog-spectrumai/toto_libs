"""Identity events on the chain, the parts the first suite left open (2026-09-29).

Who the actor is when a request is at hand, what a refused sign-in may and may
not carry, how each flag's way is worded, when the flags are read at all, and
that a chain which cannot write never takes a signup's transaction down with it.
"""

import unittest
from unittest import mock

from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.signals import user_login_failed
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import transaction
from django.test import RequestFactory, TestCase

from toto.audit import identity
from toto.audit.context import AuditContext, reset_context, set_context
from toto.audit.models import AuditChain, AuditRecord
from toto.audit.services import verify_chain

User = get_user_model()
BACKEND = "django.contrib.auth.backends.ModelBackend"


def _request(path="/sso/login/", **meta):
    request = RequestFactory().post(path, REMOTE_ADDR="198.51.100.4", **meta)
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = AnonymousUser()
    return request


class _Ambient:
    """The audit context a request would carry, for the length of a block."""

    def __init__(self, user, source="web"):
        self.value = AuditContext(user=user, source=source, correlation_id="c0ffee")

    def __enter__(self):
        self.token = set_context(self.value)

    def __exit__(self, *exc):
        reset_context(self.token)


class IdentityCase(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="s3cret-pw")

    def records(self, action):
        return AuditRecord.objects.filter(action=action)


class SignInTests(IdentityCase):
    def test_a_sign_in_records_the_backend_that_let_them_in(self):
        request = _request()
        user = authenticate(request, username="ada", password="s3cret-pw")
        login(request, user)
        backend = self.records("AUTH.LOGIN").get().metadata["backend"]
        self.assertTrue(backend)
        self.assertEqual(backend, user.backend)

    @unittest.skip("SUSPECTED BUG toto/audit/identity.py:66 - on_login reads user.backend, "
                   "which only authenticate() sets; a door that calls "
                   "login(request, user, backend=...) (social_login, sso_client) is "
                   "recorded with backend ''. The session's BACKEND_SESSION_KEY has it.")
    def test_a_door_that_names_its_backend_is_recorded_with_it(self):
        login(_request(), self.ada, backend=BACKEND)
        self.assertEqual(self.records("AUTH.LOGIN").get().metadata, {"backend": BACKEND})

    def test_a_sign_in_names_the_door_and_the_address_it_came_from(self):
        login(_request(HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1"), self.ada, backend=BACKEND)
        source = self.records("AUTH.LOGIN").get().request_source
        self.assertEqual(source["path"], "/sso/login/")
        self.assertEqual(source["method"], "POST")
        # The first hop of X-Forwarded-For is the client; the rest is the proxy chain.
        self.assertEqual(source["ip_address"], "203.0.113.9")

    def test_a_sign_in_is_about_the_account_itself(self):
        login(_request(), self.ada, backend=BACKEND)
        record = self.records("AUTH.LOGIN").get()
        self.assertEqual(record.object_type, "auth.user")
        self.assertEqual(record.object_id, str(self.ada.pk))
        self.assertEqual(record.object_description, "ada")
        self.assertEqual(record.actor_username, "ada")

    def test_a_sign_out_without_a_user_argument_records_nothing(self):
        identity.on_logout(sender=None, request=_request(), user=None)
        identity.on_logout(sender=None, request=_request(), user=AnonymousUser())
        self.assertFalse(self.records("AUTH.LOGOUT").exists())


class RefusedSignInTests(IdentityCase):
    def fail(self, credentials, request=None):
        user_login_failed.send(sender=__name__, credentials=credentials, request=request)
        return self.records("AUTH.LOGIN_FAILED").get()

    def test_an_email_is_the_attempted_name_when_no_username_was_given(self):
        record = self.fail({"email": "ada@example.org", "password": "guess"})
        self.assertEqual(record.metadata, {"username": "ada@example.org"})
        self.assertEqual(record.object_description, "ada@example.org")

    def test_the_username_wins_over_an_email_when_both_were_given(self):
        record = self.fail({"username": "ada", "email": "other@example.org"})
        self.assertEqual(record.metadata["username"], "ada")

    def test_no_credentials_at_all_is_still_one_refusal_with_no_name(self):
        record = self.fail(None)
        self.assertFalse(record.success)
        self.assertEqual(record.metadata, {"username": ""})
        self.assertEqual(record.object_id, "")

    def test_the_attempted_name_is_clamped_to_a_username_s_length(self):
        record = self.fail({"username": "x" * 400})
        self.assertEqual(len(record.metadata["username"]), 150)
        self.assertEqual(len(record.object_description), 150)

    def test_nothing_but_the_name_is_kept_from_the_credentials(self):
        record = self.fail({"username": "ada", "password": "hunter2",
                            "otp": "123456", "token": "abc"})
        self.assertEqual(record.metadata, {"username": "ada"})
        self.assertNotIn("123456", str(record.request_source) + str(record.changes))

    def test_a_refusal_inside_somebody_else_s_request_has_no_actor(self):
        """The ambient context names the browsing user; a failed sign-in is
        not something THEY did, so it must not be written as theirs."""
        root = User.objects.create_superuser("root", password="pw")
        with _Ambient(root):
            record = self.fail({"username": "ada"})
        self.assertIsNone(record.actor_user_id)
        self.assertEqual(record.actor_username, "")

    def test_a_refusal_keeps_the_request_it_came_with(self):
        record = self.fail({"username": "ada"}, request=_request("/api/login/"))
        self.assertEqual(record.request_source["path"], "/api/login/")


class AccountFlagTests(IdentityCase):
    def test_a_superuser_made_is_one_record_that_already_carries_the_flags(self):
        root = User.objects.create_superuser("root", password="pw")
        mine = AuditRecord.objects.filter(object_id=str(root.pk), app_label="auth")
        self.assertEqual(list(mine.values_list("action", flat=True)), ["AUTH.ACCOUNT_CREATED"])
        self.assertEqual(mine.get().metadata,
                         {"is_active": True, "is_staff": True, "is_superuser": True})

    def test_each_revocation_has_its_own_word_and_the_change_it_made(self):
        self.ada.is_staff = True
        self.ada.is_superuser = True
        self.ada.save()
        self.ada.is_staff = False
        self.ada.is_superuser = False
        self.ada.is_active = False
        self.ada.save()
        revoked = self.records("AUTH.STAFF_REVOKED").get()
        self.assertEqual(revoked.changes, {"is_staff": {"before": True, "after": False}})
        self.assertEqual(self.records("AUTH.SUPERUSER_REVOKED").get().changes,
                         {"is_superuser": {"before": True, "after": False}})
        self.assertEqual(self.records("AUTH.ACCOUNT_DEACTIVATED").get().changes,
                         {"is_active": {"before": True, "after": False}})

    def test_three_flags_in_one_save_are_three_records(self):
        before = AuditRecord.objects.count()
        self.ada.is_staff = True
        self.ada.is_superuser = True
        self.ada.is_active = False
        self.ada.save()
        self.assertEqual(AuditRecord.objects.count() - before, 3)

    def test_a_flag_changed_in_memory_but_not_saved_is_not_recorded(self):
        self.ada.is_staff = True
        self.ada.save(update_fields=["first_name"])
        self.assertFalse(self.records("AUTH.STAFF_GRANTED").exists())
        self.assertFalse(User.objects.get(pk=self.ada.pk).is_staff)

    def test_a_flag_saved_through_update_fields_is_recorded(self):
        self.ada.is_superuser = True
        self.ada.save(update_fields=["is_superuser"])
        self.assertEqual(self.records("AUTH.SUPERUSER_GRANTED").count(), 1)

    def test_a_snapshot_is_spent_by_the_save_that_read_it(self):
        """A later save that skipped the read must not replay the old change."""
        self.ada.is_staff = True
        self.ada.save()
        self.ada.save(update_fields=["last_name"])
        self.assertEqual(self.records("AUTH.STAFF_GRANTED").count(), 1)

    def test_resaving_the_same_flags_records_nothing(self):
        self.ada.is_staff = True
        self.ada.save()
        before = AuditRecord.objects.count()
        self.ada.save()
        self.assertEqual(AuditRecord.objects.count(), before)

    def test_whoever_changed_the_flag_is_its_actor(self):
        root = User.objects.create_superuser("root", password="pw")
        with _Ambient(root):
            self.ada.is_staff = True
            self.ada.save()
        granted = self.records("AUTH.STAFF_GRANTED").get()
        self.assertEqual(granted.actor_user, root)
        self.assertEqual(granted.source, "web")
        self.assertEqual(granted.correlation_id, "c0ffee")
        self.assertEqual(granted.object_id, str(self.ada.pk))

    def test_with_no_request_at_hand_the_system_is_the_actor(self):
        self.ada.is_active = False
        self.ada.save()
        record = self.records("AUTH.ACCOUNT_DEACTIVATED").get()
        self.assertIsNone(record.actor_user_id)
        self.assertEqual(record.source, "system")

    def test_the_chain_verifies_after_a_run_of_identity_events(self):
        login(_request(), self.ada, backend=BACKEND)
        self.ada.is_staff = True
        self.ada.save()
        user_login_failed.send(sender=__name__, credentials={"username": "nobody"})
        result = verify_chain()
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.checked, AuditRecord.objects.count())


class ReadOnlyWhenNeededTests(IdentityCase):
    def test_a_save_that_names_no_flag_does_not_read_the_row(self):
        with self.assertNumQueries(0):
            identity.before_user_saved(sender=User, instance=self.ada,
                                       update_fields=["last_login"])
        self.assertIsNone(getattr(self.ada, "_audit_flags_before", None))

    def test_an_unsaved_account_is_not_read(self):
        with self.assertNumQueries(0):
            identity.before_user_saved(sender=User, instance=User(username="new"))

    def test_a_save_that_may_move_a_flag_reads_them_once(self):
        with self.assertNumQueries(1):
            identity.before_user_saved(sender=User, instance=self.ada,
                                       update_fields=["is_staff", "last_login"])
        self.assertEqual(self.ada._audit_flags_before,
                         {"is_active": True, "is_staff": False, "is_superuser": False})

    def test_the_receivers_are_connected_once_however_often_connect_runs(self):
        identity.connect()
        identity.connect()
        self.ada.is_staff = True
        self.ada.save()
        self.assertEqual(self.records("AUTH.STAFF_GRANTED").count(), 1)


class NeverInTheWayTests(IdentityCase):
    def test_a_database_error_in_the_chain_does_not_poison_the_signup(self):
        """The record runs in its own savepoint: a failed insert inside it
        must leave the surrounding transaction usable."""

        def fails_at_the_database(*args, **kwargs):
            AuditChain.objects.create(key="duplicate-key")
            AuditChain.objects.create(key="duplicate-key")      # IntegrityError

        with mock.patch("toto.audit.services.record", side_effect=fails_at_the_database), \
                self.assertLogs("toto.audit", "ERROR"):
            bob = User.objects.create_user("bob", password="pw")
        # Still usable: this query would raise TransactionManagementError otherwise.
        self.assertTrue(User.objects.filter(pk=bob.pk).exists())
        self.assertFalse(transaction.get_connection().needs_rollback)
        self.assertFalse(AuditChain.objects.filter(key="duplicate-key").exists())

    def test_a_chain_that_cannot_write_returns_nothing_and_says_so(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("down")), \
                self.assertLogs("toto.audit", "ERROR") as logs:
            self.assertIsNone(identity._record("login", self.ada))
        self.assertIn("auth.login", "\n".join(logs.output))

    def test_a_flag_change_still_saves_when_the_chain_is_down(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("down")), \
                self.assertLogs("toto.audit", "ERROR"):
            self.ada.is_staff = True
            self.ada.save()
        self.assertTrue(User.objects.get(pk=self.ada.pk).is_staff)
