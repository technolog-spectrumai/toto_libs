"""`erase_user` and `community_members` — the console's account tools (2026-09-29).

The erase takes everything only theirs and keeps what is sealed: after it the
audit chain still verifies, and the chain records the erasure."""

import io
import json
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth import login
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings

from toto.audit.models import AuditRecord
from toto.audit.services import verify_chain
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community

User = get_user_model()


def run(*args):
    out = io.StringIO()
    try:
        call_command(*args, stdout=out)
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def signed_in(user):
    request = RequestFactory().post("/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = AnonymousUser()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="erase-user-"))
class EraseUserTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser("root", "r@example.com", "pw")
        self.ada = User.objects.create_user("ada", "ada@example.com", "pw")
        self.internal = Clearance.objects.create(name="internal", slug="internal")
        Person.objects.create(user=self.ada, display_name="Ada").clearances.add(self.internal)
        signed_in(self.ada)                                  # an AUTH.LOGIN with ada as actor

    def file(self):
        from toto.vault.models import Bucket, VaultFile

        bucket = Bucket.objects.create(name="Ada's", slug="adas", owner=self.ada)
        f = VaultFile(owner=self.ada, title="notes.txt", key="notes", file_type="text", bucket=bucket)
        f.file.save("notes.txt", ContentFile(b"secret"), save=True)
        return f

    def test_the_report_names_what_would_go_and_changes_nothing(self):
        self.file()
        code, out = run("erase_user", "ada")
        self.assertEqual(code, 0)
        self.assertFalse(out["erased"])
        deleted = out["report"]["deleted"]
        self.assertEqual(deleted["auth.User"], 1)
        self.assertEqual(deleted["people.Person"], 1)
        self.assertEqual(deleted["vault.VaultFile"], 1)
        self.assertTrue(User.objects.filter(username="ada").exists())

    def test_the_report_names_only_the_tables_with_rows(self):
        """It listed every related table, nearly all at 0 (2026-10-01): the
        collector hands back a queryset for every relation, empty or not."""
        code, out = run("erase_user", "ada")
        report = out["report"]
        self.assertEqual(report["deleted"]["people.Person"], 1)
        self.assertNotIn(0, report["deleted"].values())
        self.assertNotIn(0, report["detached"].values())
        self.assertNotIn("vault.VaultFile", report["deleted"])        # she has none

    def test_erasing_takes_everything_theirs_and_the_chain_still_verifies(self):
        import os

        f = self.file()
        path = f.file.path
        code, out = run("erase_user", "ada", "--confirm", "ada")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["erased"])
        self.assertFalse(User.objects.filter(username="ada").exists())
        self.assertFalse(Person.objects.filter(display_name="Ada").exists())
        self.assertEqual(self.internal.members.count(), 0)
        self.assertFalse(os.path.exists(path))               # the bytes too
        # Sealed records stay, by username — and the chain still verifies.
        self.assertTrue(AuditRecord.objects.filter(action="AUTH.LOGIN", actor_username="ada").exists())
        self.assertTrue(verify_chain().ok)
        erased = AuditRecord.objects.filter(action="AUTH.ACCOUNT_ERASED").get()
        self.assertEqual(erased.object_description, "ada")
        self.assertEqual(erased.source, "console")

    def test_refusals(self):
        self.assertEqual(run("erase_user", "nobody")[0], 1)
        code, out = run("erase_user", "ada", "--confirm", "Ada")
        self.assertEqual(code, 1)
        self.assertTrue(User.objects.filter(username="ada").exists())
        code, out = run("erase_user", "root", "--confirm", "root")
        self.assertEqual(code, 1)
        self.assertIn("last active superuser", out["error"])
        self.assertTrue(User.objects.filter(username="root").exists())

    def test_deleting_an_account_anywhere_keeps_the_chain_verifying(self):
        """The bug under the tool: SET_NULL rewrote a sealed field."""
        self.ada.delete()
        self.assertTrue(verify_chain().ok)


class CommunityMembersTests(TestCase):
    def setUp(self):
        self.devs = Community.objects.create(name="devs", slug="devs")
        self.internal = Clearance.objects.create(name="internal", slug="internal")
        self.ada = User.objects.create_user("ada", "ada@example.com", "pw")

    def test_list_names_the_two_kinds_apart(self):
        code, out = run("community_members", "list")
        self.assertEqual(code, 0)
        self.assertEqual([c["slug"] for c in out["communities"]], ["devs"])
        self.assertEqual([c["slug"] for c in out["clearances"]], ["internal"])

    def test_join_makes_the_person_and_the_memberships(self):
        code, out = run("community_members", "join", "ada", "--community", "devs", "--clearance", "internal")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["person_created"])
        person = Person.objects.get(user=self.ada)
        self.assertEqual({c.slug for c in person.communities.all()}, {"devs"})
        self.assertEqual({c.slug for c in person.clearances.all()}, {"internal"})
        self.assertEqual(AuditRecord.objects.filter(action="SOCIALHUB.MEMBER_ADDED").count(), 1)
        self.assertEqual(AuditRecord.objects.filter(
            action="SOCIALHUB.CLEARANCE_MEMBER_ADDED").count(), 1)

    def test_the_kinds_are_not_interchangeable_and_nothing_half_happens(self):
        code, out = run("community_members", "join", "ada", "--community", "internal", "--clearance", "devs")
        self.assertEqual(code, 1)
        self.assertIn("community 'internal'", out["error"])
        self.assertFalse(Person.objects.filter(user=self.ada).exists())
        self.assertEqual(run("community_members", "join", "nobody", "--community", "devs")[0], 1)
