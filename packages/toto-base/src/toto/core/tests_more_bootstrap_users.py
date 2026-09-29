"""`bootstrap_users`, the parts its first suite left open (2026-09-29): the
address an account gets when it is given none, the real stdin pipe, the plan
ladder a superuser is put on, and what the audit chain says about the run."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from toto.audit.models import AuditRecord
from toto.core.management.commands.bootstrap_users import MAX_USERS, _default_email

User = get_user_model()


def run(payload, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "users.json"
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                        encoding="utf-8")
        try:
            call_command("bootstrap_users", stdin=str(path), stdout=out, stderr=err, **kwargs)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def rows(*users):
    return {"users": list(users)}


class DefaultEmailTests(SimpleTestCase):
    def env(self, **values):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ADMIN_EMAIL", "ADMIN_USERNAME", "PLATFORM_DOMAIN")}
        clean.update(values)
        return mock.patch.dict(os.environ, clean, clear=True)

    def test_the_admin_row_gets_the_admin_address(self):
        with self.env(ADMIN_EMAIL=" ops@example.org ", PLATFORM_DOMAIN="zen.example.org"):
            self.assertEqual(_default_email("admin"), "ops@example.org")

    def test_the_admin_address_is_not_handed_to_anybody_else(self):
        with self.env(ADMIN_EMAIL="ops@example.org", PLATFORM_DOMAIN="zen.example.org"):
            self.assertEqual(_default_email("ada"), "ada@zen.example.org")

    def test_the_admin_username_is_whatever_the_host_calls_it(self):
        with self.env(ADMIN_EMAIL="ops@example.org", ADMIN_USERNAME="root"):
            self.assertEqual(_default_email("root"), "ops@example.org")
            self.assertEqual(_default_email("admin"), "admin@localhost.invalid")

    def test_a_domain_written_as_a_url_is_reduced_to_its_host(self):
        with self.env(PLATFORM_DOMAIN="https://zen.example.org/sso/"):
            self.assertEqual(_default_email("ada"), "ada@zen.example.org")

    def test_no_domain_or_a_loopback_one_is_the_reserved_invalid_domain(self):
        for domain in ("", "localhost", "127.0.0.1", "http://localhost/"):
            with self.subTest(domain=domain), self.env(PLATFORM_DOMAIN=domain):
                self.assertEqual(_default_email("ada"), "ada@localhost.invalid")


class CreateTests(TestCase):
    def test_an_account_given_no_address_gets_a_derived_one(self):
        with mock.patch.dict(os.environ, {"PLATFORM_DOMAIN": "zen.example.org"}):
            run(rows({"username": "vera", "password": "pw"}))
        self.assertEqual(User.objects.get(username="vera").email, "vera@zen.example.org")

    def test_a_given_address_is_kept_as_typed_less_the_spaces(self):
        run(rows({"username": "vera", "password": "pw", "email": "  vera@real.example "}))
        self.assertEqual(User.objects.get(username="vera").email, "vera@real.example")

    def test_a_superuser_is_never_left_without_an_address(self):
        """Gitea refuses to register an OIDC identity with an empty email claim."""
        run(rows({"username": "sue", "password": "pw", "superuser": True}))
        self.assertTrue(User.objects.get(username="sue").email)

    def test_the_username_is_trimmed(self):
        run(rows({"username": "  vera  ", "password": "pw"}))
        self.assertTrue(User.objects.filter(username="vera").exists())

    def test_exactly_the_maximum_is_accepted(self):
        code, _, _ = run(rows(*[{"username": f"u{n}", "password": "pw"} for n in range(MAX_USERS)]))
        self.assertEqual(code, 0)
        self.assertEqual(User.objects.filter(username__startswith="u").count(), MAX_USERS)

    def test_each_row_reports_its_level(self):
        _, out, _ = run(rows({"username": "v", "password": "pw"},
                             {"username": "s", "password": "pw", "staff": True},
                             {"username": "r", "password": "pw", "superuser": True}))
        self.assertIn("v: created (viewer)", out)
        self.assertIn("s: created (staff)", out)
        self.assertIn("r: created (superuser)", out)

    def test_a_null_row_is_a_reported_failure_not_a_crash(self):
        code, _, err = run(rows(None, {"username": "ok", "password": "pw"}))
        self.assertEqual(code, 1)
        self.assertIn("without username or password", err)
        self.assertTrue(User.objects.filter(username="ok").exists())

    def test_a_payload_without_users_does_nothing(self):
        for payload in ("", "{}", "null", '{"users": null}'):
            with self.subTest(payload=payload):
                code, out, _ = run(payload)
                self.assertEqual(code, 0)
                self.assertIn("nothing to do", out)
        self.assertFalse(User.objects.exists())

    @unittest.skip("SUSPECTED BUG toto/core/management/commands/bootstrap_users.py:120,132 - "
                   "a JSON payload that is a list, or a row that is not an object "
                   "({'users': ['anna']}), crashes with AttributeError and a traceback "
                   "instead of the clean failure bad JSON gets.")
    def test_a_payload_of_the_wrong_shape_is_a_clean_failure(self):
        for payload in ('{"users": ["anna"]}', '[{"username": "a", "password": "b"}]'):
            with self.subTest(payload=payload):
                code, _, _ = run(payload)
                self.assertEqual(code, 1)
        self.assertFalse(User.objects.exists())

    def test_the_real_pipe_is_read_when_no_file_is_named(self):
        pipe = io.StringIO(json.dumps(rows({"username": "piped", "password": "pw"})))
        with mock.patch("sys.stdin", pipe):
            call_command("bootstrap_users", stdout=io.StringIO(), stderr=io.StringIO())
        self.assertTrue(User.objects.get(username="piped").check_password("pw"))


class ChainTests(TestCase):
    def test_every_created_account_is_on_the_chain(self):
        run(rows({"username": "a1", "password": "pw"}, {"username": "a2", "password": "pw"}))
        created = AuditRecord.objects.filter(action="AUTH.ACCOUNT_CREATED")
        self.assertEqual(set(created.values_list("object_description", flat=True)), {"a1", "a2"})

    def test_a_reset_that_takes_a_flag_away_is_recorded_as_such(self):
        User.objects.create_superuser("anna", "anna@example.org", "old")
        run(rows({"username": "anna", "password": "new"}), reset_existing=True)
        self.assertTrue(AuditRecord.objects.filter(action="AUTH.SUPERUSER_REVOKED",
                                                   object_description="anna").exists())
        self.assertTrue(AuditRecord.objects.filter(action="AUTH.STAFF_REVOKED",
                                                   object_description="anna").exists())

    def test_no_password_reaches_the_chain(self):
        run(rows({"username": "pat", "password": "hunter2-secret"}))
        for row in AuditRecord.objects.all():
            self.assertNotIn("hunter2-secret",
                             json.dumps([row.metadata, row.changes, row.object_description]))


class PlansTests(TestCase):
    def setUp(self):
        if not apps.is_installed("toto.subscriptions"):
            self.skipTest("no plans on this host")

    def test_a_superuser_made_here_holds_the_admin_plan(self):
        from toto.subscriptions.models import superuser_plan_active

        run(rows({"username": "sue", "password": "pw", "superuser": True}))
        self.assertTrue(superuser_plan_active(User.objects.get(username="sue")))

    def test_a_ladder_that_fails_still_leaves_the_accounts_and_says_so(self):
        target = ("toto.subscriptions.management.commands.bootstrap_plans.Command.handle")
        with mock.patch(target, side_effect=RuntimeError("ladder broken")):
            code, _, err = run(rows({"username": "sue", "password": "pw", "superuser": True}))
        self.assertEqual(code, 0)
        self.assertIn("bootstrap_plans failed: ladder broken", err)
        self.assertTrue(User.objects.filter(username="sue", is_superuser=True).exists())
