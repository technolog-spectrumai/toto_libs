"""A copy of one member's data (2026-10-01, RODO): ``toto.core.personal_data``
and the console's ``export_user``.

What must hold: the member's own rows are in it, another member's are not,
and no password hash, session key or token is — anywhere in the zip. Their
own vault files come with their bytes, but only those they may still read
under the bucket clearances; an encrypted file is listed without its bytes.
"""

import io
import json
import os
import tempfile
import zipfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.audit.models import AuditRecord
from toto.audit.services import record
from toto.core.models import UserSession
from toto.core.personal_data import SECRET_FIELD, row_of, write_zip
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community, PrivacyAcceptance

User = get_user_model()


def _zip(user) -> zipfile.ZipFile:
    buf = io.BytesIO()
    write_zip(user, buf)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def _json(zf, name):
    return json.loads(zf.read(name))


def _all_text(zf) -> str:
    return "\n".join(zf.read(n).decode("utf-8", "replace") for n in zf.namelist())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="personal-data-"))
class PersonalDataTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", "ada@example.test", "Ada-pass-123",
                                            first_name="Ada")
        self.bob = User.objects.create_user("bob", "bob@example.test", "Bob-pass-123")
        self.devs = Community.objects.create(name="Devs", slug="devs")
        self.internal = Clearance.objects.create(name="internal", slug="internal")
        self.person = Person.objects.create(user=self.ada, display_name="Ada L.",
                                            phone="+48 600 000 001")
        self.person.communities.add(self.devs)
        self.person.clearances.add(self.internal)
        Person.objects.create(user=self.bob, display_name="Bob B.", phone="+48 600 000 002")
        PrivacyAcceptance.objects.create(person=self.person, version=1)
        UserSession.objects.create(user=self.ada, session_key="adasessionkey0001",
                                   ip="203.0.113.7", user_agent="Firefox")
        UserSession.objects.create(user=self.bob, session_key="bobsessionkey0001",
                                   ip="203.0.113.8", user_agent="Chrome")
        record("auth.login", app_label="auth", actor_user=self.ada, description="ada in")
        record("auth.login", app_label="auth", actor_user=self.bob, description="bob in")

    def _file(self, owner, bucket, title, data, **extra):
        from toto.vault.models import VaultFile

        f = VaultFile(owner=owner, title=title, key=title.replace(".", "-"),
                      file_type="text", bucket=bucket, **extra)
        f.file.save(title, ContentFile(data), save=True)
        return f

    def _bucket(self, slug, owner):
        from toto.vault.models import Bucket

        return Bucket.objects.create(name=slug, slug=slug, owner=owner)

    def test_their_own_rows_are_in_it(self):
        zf = _zip(self.ada)
        self.assertEqual(_json(zf, "account.json")[0]["username"], "ada")
        self.assertEqual(_json(zf, "profile.json")[0]["phone"], "+48 600 000 001")
        self.assertEqual([c["slug"] for c in _json(zf, "communities.json")], ["devs"])
        self.assertEqual([c["slug"] for c in _json(zf, "clearances.json")], ["internal"])
        self.assertEqual([a["version"] for a in _json(zf, "privacy_acceptances.json")], [1])
        self.assertEqual([s["ip"] for s in _json(zf, "sessions.json")], ["203.0.113.7"])
        self.assertIn("ada in", [r["object_description"] for r in _json(zf, "audit_records.json")])
        self.assertIn("README.txt", zf.namelist())
        self.assertIn("account.csv", zf.namelist())
        self.assertIn("sessions.csv", zf.read("README.txt").decode())

    def test_another_members_rows_are_not(self):
        text = _all_text(_zip(self.ada))
        for theirs in ("bob@example.test", "+48 600 000 002", "203.0.113.8", "bob in", "Chrome"):
            self.assertNotIn(theirs, text)

    def test_no_password_hash_session_key_or_secret_column(self):
        zf = _zip(self.ada)
        text = _all_text(zf)
        self.assertNotIn(self.ada.password, text)
        self.assertNotIn("pbkdf2", text)
        self.assertNotIn("adasessionkey0001", text)
        for name in zf.namelist():
            if name.endswith(".json"):
                for row in _json(zf, name):
                    for column in row:
                        self.assertIsNone(SECRET_FIELD.search(column), (name, column))

    def test_the_secret_rule_drops_credential_columns(self):
        row = row_of(UserSession.objects.get(user=self.ada))
        self.assertNotIn("session_key", row)
        self.assertEqual(row["user_agent"], "Firefox")
        for name in ("password", "record_hash", "previous_hash", "api_token", "body_sealed",
                     "idempotency_key", "secret_key"):
            self.assertIsNotNone(SECRET_FIELD.search(name), name)
        for name in ("plan_key", "phone", "email", "display_name", "code"):
            self.assertIsNone(SECRET_FIELD.search(name), name)

    def test_applications_leave_the_verification_code_out(self):
        from datetime import timedelta

        from django.utils import timezone

        from toto.socialhub.models import MembershipApplication

        app = MembershipApplication.objects.create(
            email="ada@example.test", community=self.devs,
            expires_at=timezone.now() + timedelta(days=1), privacy_version=1)
        zf = _zip(self.ada)
        rows = _json(zf, "applications.json")
        self.assertEqual(rows[0]["community"], "Devs")
        self.assertNotIn("code", rows[0])
        self.assertNotIn(app.code, _all_text(zf))

    def test_their_own_readable_files_come_with_their_bytes(self):
        mine = self._bucket("personal-ada", self.ada)
        theirs = self._bucket("personal-bob", self.bob)
        self._file(self.ada, mine, "notes.txt", b"ada's own notes")
        self._file(self.bob, theirs, "bobs.txt", b"bob's notes")
        self._file(self.bob, mine, "dropped.txt", b"bob filed this in ada's bucket")
        zf = _zip(self.ada)
        index = _json(zf, "files/index.json")
        self.assertEqual([r["title"] for r in index], ["notes.txt"])
        self.assertEqual(zf.read(index[0]["path_in_zip"]), b"ada's own notes")
        self.assertTrue(index[0]["path_in_zip"].startswith("files/personal-ada/"))
        self.assertNotIn("bob's notes", _all_text(zf))

    def test_a_file_in_a_bucket_kept_from_them_is_left_out(self):
        from toto.vault.models import BucketClearance

        confidential = Clearance.objects.create(name="confidential", slug="confidential")
        kept = self._bucket("board", self.ada)
        BucketClearance.objects.create(bucket=kept, clearance=confidential)
        self._file(self.ada, kept, "minutes.txt", b"board minutes")
        zf = _zip(self.ada)
        self.assertEqual(_json(zf, "files/index.json"), [])
        self.assertNotIn("board minutes", _all_text(zf))

    def test_an_encrypted_file_is_listed_without_its_bytes(self):
        bucket = self._bucket("personal-ada", self.ada)
        self._file(self.ada, bucket, "sealed.txt", b"CIPHERTEXT-BYTES", is_encrypted=True)
        zf = _zip(self.ada)
        row, = _json(zf, "files/index.json")
        self.assertEqual(row["path_in_zip"], "")
        self.assertIn("encrypted", row["note"])
        self.assertNotIn("CIPHERTEXT-BYTES", _all_text(zf))

    def test_a_member_with_no_profile_still_gets_their_account(self):
        carol = User.objects.create_user("carol", "carol@example.test", "Carol-pass-123")
        zf = _zip(carol)
        self.assertEqual(_json(zf, "account.json")[0]["username"], "carol")
        self.assertNotIn("profile.json", zf.namelist())


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command(*args, stdout=out, stderr=err)
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue(), err.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="export-user-"))
class ExportUserCommandTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", "ada@example.test", "Ada-pass-123")
        Person.objects.create(user=self.ada, display_name="Ada")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_it_writes_the_zip_privately_and_reports_on_one_line(self):
        path = os.path.join(self.dir, "ada.zip")
        code, out, _ = run("export_user", "--user", "ada", "--out", path)
        self.assertEqual(code, 0)
        report = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(report["ok"])
        self.assertEqual(report["tables"]["account"], 1)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(json.loads(zf.read("account.json"))[0]["username"], "ada")

    def test_it_is_on_the_chain_with_counts_only(self):
        run("export_user", "--user", "ada", "--out", os.path.join(self.dir, "a.zip"))
        rec = AuditRecord.objects.get(action="PRIVACY.EXPORT_READY")
        self.assertEqual(rec.source, "console")
        self.assertIsNone(rec.actor_user)
        self.assertEqual(rec.metadata["user"], self.ada.pk)
        self.assertEqual(rec.metadata["tables"]["account"], 1)

    def test_an_existing_file_is_never_overwritten(self):
        path = os.path.join(self.dir, "there.zip")
        with open(path, "w") as fh:
            fh.write("keep me")
        code, out, _ = run("export_user", "--user", "ada", "--out", path)
        self.assertEqual(code, 1)
        self.assertIn("exists", json.loads(out.strip())["error"])
        with open(path) as fh:
            self.assertEqual(fh.read(), "keep me")

    def test_an_unknown_account_is_refused(self):
        code, out, _ = run("export_user", "--user", "nobody", "--out",
                           os.path.join(self.dir, "x.zip"))
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.strip())["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "x.zip")))

    def test_dash_puts_the_zip_on_stdout_and_the_report_on_stderr(self):
        raw = io.BytesIO()
        fake = mock.Mock()
        fake.buffer = raw
        with mock.patch("toto.core.management.commands.export_user.sys.stdout", fake):
            code, out, err = run("export_user", "--user", "ada", "--out", "-")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertTrue(json.loads(err.strip().splitlines()[-1])["ok"])
        with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as zf:
            self.assertIn("README.txt", zf.namelist())
