"""The start-account bootstrap: what a deployment tool relies on.

A builder's Users tab is the caller — it pipes JSON in and shows the report
back. These are the promises it is written against.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

User = get_user_model()


def run(payload, **kwargs):
    """Call the command with `payload` on its (file-shaped) stdin."""
    from io import StringIO

    out, err = StringIO(), StringIO()
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "users.json"
        path.write_text(payload if isinstance(payload, str)
                        else json.dumps(payload), encoding="utf-8")
        try:
            call_command("bootstrap_users", stdin=str(path), stdout=out,
                         stderr=err, **kwargs)
        except SystemExit as exc:
            err.write(f"\nexit {exc.code}")
    return out.getvalue(), err.getvalue()


def rows(*users):
    return {"users": list(users)}


class BootstrapUsersTests(TestCase):
    def test_a_viewer_is_a_plain_active_account(self):
        run(rows({"username": "vera", "password": "pw"}))
        user = User.objects.get(username="vera")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password("pw"))

    def test_staff_gets_the_writing_flag(self):
        run(rows({"username": "stan", "password": "pw", "staff": True}))
        user = User.objects.get(username="stan")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_board_is_a_synonym_for_staff(self):
        """placidia's truth book calls the writing role `board` (§Dk2)."""
        run(rows({"username": "bea", "password": "pw", "board": True}))
        self.assertTrue(User.objects.get(username="bea").is_staff)

    def test_a_superuser_is_staff_too(self):
        run(rows({"username": "sue", "password": "pw", "superuser": True}))
        user = User.objects.get(username="sue")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)

    def test_an_existing_username_is_reported_and_left_alone(self):
        """The difference from create_user, and the reason this exists."""
        existing = User.objects.create_user("anna", password="original")
        existing.is_staff = True
        existing.save()

        out, _ = run(rows({"username": "anna", "password": "taken-over",
                           "superuser": True}))
        anna = User.objects.get(username="anna")
        self.assertTrue(anna.check_password("original"))
        self.assertFalse(anna.is_superuser)
        self.assertIn("already present", out)

    def test_no_password_ever_reaches_the_output(self):
        out, err = run(rows({"username": "pat", "password": "hunter2"},
                            {"username": "", "password": "hunter2"}))
        self.assertNotIn("hunter2", out + err)

    def test_more_than_the_maximum_is_refused_whole(self):
        out, err = run(rows(*[{"username": f"u{n}", "password": "pw"}
                              for n in range(6)]))
        self.assertIn("at most", err)
        self.assertEqual(User.objects.count(), 0)

    def test_a_bad_row_fails_the_run_but_the_good_rows_are_created(self):
        out, err = run(rows({"username": "good", "password": "pw"},
                            {"username": "nopassword", "password": ""}))
        self.assertTrue(User.objects.filter(username="good").exists())
        self.assertIn("exit 1", err)

    def test_an_invalid_username_is_reported_not_crashed(self):
        out, err = run(rows({"username": "not a username", "password": "pw"}))
        self.assertIn("not a valid username", err)
        self.assertEqual(User.objects.count(), 0)

    def test_garbage_on_stdin_is_a_clean_failure(self):
        out, err = run("{not json")
        self.assertIn("not valid JSON", err)
        self.assertEqual(User.objects.count(), 0)

    def test_an_empty_payload_does_nothing(self):
        out, err = run({"users": []})
        self.assertIn("nothing to do", out)
        self.assertEqual(User.objects.count(), 0)
