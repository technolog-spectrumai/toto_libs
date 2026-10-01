"""The export's audit records about the member that somebody else wrote
(2026-10-01, the review of stage 35; ``personal_data._audit``,
``toto.audit.queries.records_about``).

The copy held only the records the member made. Now a second table holds the
ones made about their account — an administrator changing it, a sign-in tried
with their name and the pause that followed, a clearance given, an
application handled — without the other side's address and browser
(``request_source``, and the address a pause names) and without a secret;
the README says which table is which.

    manage.py test toto.core.tests_records_about_you
"""

import io
import json
import zipfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from toto.audit import identity
from toto.audit.context import AuditContext, reset_context, set_context, suppress_audit
from toto.audit.services import record
from toto.core.personal_data import SECRET_FIELD, write_zip
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community, MembershipApplication

User = get_user_model()

ADMIN_ADDRESS, ADMIN_BROWSER = "198.51.100.9", "AdminBrowser/1.0"
GUESSER_ADDRESS, GUESSER_BROWSER = "203.0.113.66", "GuessBot/2.0"
OWN_ADDRESS, OWN_BROWSER = "192.0.2.10", "AdaBrowser/3.0"


def _zip(user) -> zipfile.ZipFile:
    buf = io.BytesIO()
    write_zip(user, buf)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def _text(zf) -> str:
    return "\n".join(zf.read(n).decode("utf-8", "replace") for n in zf.namelist())


class RecordsAboutYouTests(TestCase):
    def setUp(self):
        # Their AUTH.ACCOUNT_CREATED would be about them too; each test makes
        # its own records.
        with suppress_audit():
            self.ada = User.objects.create_user("ada", "ada@example.test", "Ada-pass-123")
            self.bob = User.objects.create_user("bob", "bob@example.test", "Bob-pass-123")
            self.root = User.objects.create_superuser("root", "root@example.test",
                                                      "Root-pass-123")
            self.person = Person.objects.create(user=self.ada, display_name="Ada")
        self.factory = RequestFactory()

    def request(self, address, browser, path="/admin/auth/user/1/change/"):
        return self.factory.post(path, REMOTE_ADDR=address, HTTP_USER_AGENT=browser)

    def as_admin(self, change):
        """``change()`` done by root from the admin, as the audit middleware
        would put it: root is the actor and its request the source."""
        token = set_context(AuditContext(user=self.root, source="admin",
                                         request=self.request(ADMIN_ADDRESS, ADMIN_BROWSER)))
        try:
            change()
        finally:
            reset_context(token)

    def about(self, zf=None):
        return json.loads((zf or _zip(self.ada)).read("audit_records_about_you.json"))

    def actions(self, rows):
        return [row["action"] for row in rows]

    def test_an_admin_changing_their_account_is_in_it_without_the_admins_address(self):
        def grant():
            self.ada.is_staff = True
            self.ada.save()

        self.as_admin(grant)
        zf = _zip(self.ada)
        row, = [r for r in self.about(zf) if r["action"] == "AUTH.STAFF_GRANTED"]
        self.assertEqual((row["actor_username"], row["object_id"]), ("root", str(self.ada.pk)))
        self.assertEqual(row["changes"], {"is_staff": {"before": False, "after": True}})
        self.assertNotIn("request_source", row)
        text = _text(zf)
        self.assertNotIn(ADMIN_ADDRESS, text)
        self.assertNotIn(ADMIN_BROWSER, text)
        made = json.loads(zf.read("audit_records.json"))
        self.assertNotIn("AUTH.STAFF_GRANTED", self.actions(made))

    def test_a_pause_naming_them_is_in_it_without_the_guessers_address(self):
        guesser = self.request(GUESSER_ADDRESS, GUESSER_BROWSER, path="/login/")
        identity.on_login_failed(None, {"username": "ADA@example.test"}, request=guesser)
        identity.on_signin_locked(scope="account_address", address=GUESSER_ADDRESS,
                                  failures=5, minutes=15, username="ada", request=guesser)
        zf = _zip(self.ada)
        rows = self.about(zf)
        self.assertEqual(self.actions(rows), ["AUTH.LOGIN_FAILED", "AUTH.LOCKED"])
        locked = rows[1]
        self.assertEqual(locked["metadata"],
                         {"scope": "account_address", "failures": 5, "minutes": 15,
                          "username": "ada"})
        text = _text(zf)
        self.assertNotIn(GUESSER_ADDRESS, text)
        self.assertNotIn(GUESSER_BROWSER, text)

    def test_a_clearance_given_and_an_application_handled_are_in_it(self):
        internal = Clearance.objects.create(name="internal", slug="internal")
        self.as_admin(lambda: self.person.clearances.add(internal))
        guild = Community.objects.create(name="Guild", slug="guild")
        application = MembershipApplication.objects.create(
            email="ada@example.test", community=guild,
            expires_at=timezone.now() + timedelta(days=7))
        application.status = "verified"
        application.save()
        rows = self.about()
        self.assertIn("SOCIALHUB.CLEARANCE_MEMBER_ADDED", self.actions(rows))
        handled = [r for r in rows if r["object_type"] == "socialhub.membershipapplication"]
        self.assertEqual(self.actions(handled), ["SOCIALHUB.APPLICATION_SUBMITTED",
                                                 "SOCIALHUB.APPLICATION_VERIFIED"])

    def test_what_they_did_stays_in_their_own_table_with_their_own_address(self):
        record("auth.login", app_label="auth", object_type="auth.user",
               object_id=str(self.ada.pk), description="ada", actor_user=self.ada,
               request=self.request(OWN_ADDRESS, OWN_BROWSER, path="/login/"))
        zf = _zip(self.ada)
        made, = json.loads(zf.read("audit_records.json"))
        self.assertEqual(made["request_source"]["ip_address"], OWN_ADDRESS)
        self.assertEqual(self.about(zf), [])

    def test_records_about_another_member_are_not_in_it(self):
        def grant_bob():
            self.bob.is_staff = True
            self.bob.save()

        self.as_admin(grant_bob)
        bob_guess = self.request(GUESSER_ADDRESS, GUESSER_BROWSER, path="/login/")
        identity.on_login_failed(None, {"username": "bob"}, request=bob_guess)
        # A pause of a whole address names no one.
        identity.on_signin_locked(scope="address", address=GUESSER_ADDRESS, failures=50,
                                  minutes=60, request=bob_guess)
        self.assertEqual(self.about(), [])

    def test_no_secret_column_and_the_readme_tells_the_tables_apart(self):
        record("auth.token_refused", app_label="auth", object_type="auth.user",
               object_id=str(self.ada.pk), description="ada", success=False,
               metadata={"door": "api", "reason": "session_hash", "api_token": "tok-123"},
               dedupe_key="refused-once")
        zf = _zip(self.ada)
        row, = self.about(zf)
        for column in row:
            self.assertIsNone(SECRET_FIELD.search(column), column)
        self.assertNotIn("tok-123", _text(zf))
        readme = zf.read("README.txt").decode()
        self.assertIn("audit_records.csv / audit_records.json (0): What the platform's "
                      "audit trail records you doing", readme)
        self.assertIn("audit_records_about_you.csv / audit_records_about_you.json (1): "
                      "What the audit trail records others doing about you", readme)
        self.assertIn("Their address and browser are left out", readme)
