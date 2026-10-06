"""The vault's one-field "New" (stage 62b, 2026-10-06): the third word of
``VAULT_STORAGE_ONLY_OPENS``, the name's rule, and the door that makes the
file the way an upload is made (``toto.vault.new_file``).

The endings and what a file starts with are an editor plugin's to say, so
these tests register one of their own; which endings a real editor offers
and what its files start with is pinned on the host that serves it.
"""

import contextlib
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.vault import new_file
from toto.vault.models import Bucket, VaultDirectory, VaultFile, VaultUsageEvent
from toto.vault.plugins import VaultEditorPlugin


class _Editor(VaultEditorPlugin):
    """An editor that says what a new file of its type may be called and
    what it starts with. ``only`` closes it to everybody else (a plan)."""

    def __init__(self, file_type, endings, content="", only=""):
        self.file_type = file_type
        self.new_file_extensions = endings
        self.content = content
        self.only = only

    def is_open_to(self, user):
        return not self.only or user.get_username() == self.only

    def get_editor_url(self, vault_file):
        return f"/nowhere/edit/{vault_file.pk}/"

    def new_file_content(self, name):
        return self.content.replace("{name}", name)


def _editors(only=""):
    return {
        "markdown": _Editor("markdown", (".md", ".markdown"), only=only),
        "pxml": _Editor("pxml", (".pxml",), "<presentation title=\"{name}\"/>\n", only=only),
        "json": _Editor("json", (".json", ".sheet.json"), only=only),
        # ".js" is no ending of its own in the vault's table: a text file.
        "text": _Editor("text", (".txt", ".js"), only=only),
        # Named for a type an upload of that name would not get: not offered.
        "svg": _Editor("svg", (".png", "svg", "."), only=only),
    }


@contextlib.contextmanager
def _registered(editors):
    with mock.patch.dict(VaultEditorPlugin.registry, editors, clear=True):
        yield


OPEN = dict(VAULT_STORAGE_ONLY=True, VAULT_FILE_EDITS=True,
            VAULT_STORAGE_ONLY_OPENS=("play", "edit", "new"))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-new-file-"), **OPEN)
class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("maker", "m@x.com", "pw")
        cls.reader = User.objects.create_user("reader", "r@x.com", "pw")
        cls.bucket = Bucket.objects.create(name="Made", owner=cls.owner, slug="new-file-made")
        cls.directory = VaultDirectory.objects.create(
            bucket=cls.bucket, name="drafts", owner=cls.owner)

    def setUp(self):
        self.client.force_login(self.owner)
        stack = contextlib.ExitStack()
        stack.enter_context(_registered(_editors()))
        self.addCleanup(stack.close)

    def make(self, name, directory="here", **extra):
        data = {"name": name, **extra}
        if directory == "here":
            data["directory_id"] = self.directory.pk
        elif directory is not None:
            data["directory_id"] = directory
        return self.client.post(reverse("vault:new_file"), data)

    def content(self, vault_file):
        vault_file.file.open("rb")
        try:
            return vault_file.file.read()
        finally:
            vault_file.file.close()


class NameRuleTests(TestCase):
    def test_one_plain_name_passes(self):
        for name in ("notes.md", "Plan 2026 v1.2.md", "zażółć.txt", "a.b.c.json"):
            with self.subTest(name=name):
                self.assertEqual(new_file.name_refusal(name), "")

    def test_what_is_refused(self):
        for name in ("", "../a.md", "a/b.md", "a\\b.md", "..", "a..md", " a.md",
                     "a.md ", "a\n.md", "a\x00.md", "a\x7f.md", "a .md",
                     ".md", ".hidden.md", "/etc/passwd", "x" * 118 + ".md"):
            with self.subTest(name=name):
                self.assertNotEqual(new_file.name_refusal(name), "")
        self.assertEqual(new_file.name_refusal("x" * 117 + ".md"), "")

    def test_the_length_in_the_sentence_is_the_length_of_the_rule(self):
        self.assertEqual(new_file.MAX_NAME_LENGTH, 120)
        self.assertIn("120", new_file.name_refusal("x" * 200 + ".md"))

    def test_the_longest_ending_wins_and_a_bare_ending_is_none(self):
        endings = {".json", ".sheet.json", ".md"}
        self.assertEqual(new_file.ending_of("a.sheet.json", endings), ".sheet.json")
        self.assertEqual(new_file.ending_of("A.JSON", endings), ".json")
        self.assertEqual(new_file.ending_of("a.txt", endings), "")
        self.assertEqual(new_file.ending_of("a", endings), "")
        self.assertEqual(new_file.ending_of(".md", endings), "")


class OfferedTests(_Base):
    def test_the_endings_are_the_plugins_own_and_honest_about_the_type(self):
        self.assertEqual(new_file.offered_extensions(self.owner),
                         [".js", ".json", ".markdown", ".md", ".pxml",
                          ".sheet.json", ".txt"])

    def test_a_plugin_closed_to_a_member_offers_them_nothing(self):
        with _registered(_editors(only="maker")):
            self.assertEqual(new_file.offered_extensions(self.reader), [])
            self.assertIn(".md", new_file.offered_extensions(self.owner))
            self.assertIn(".md", new_file.offered_extensions(None))

    def test_named_nowhere_nothing_is_offered(self):
        with self.settings(VAULT_STORAGE_ONLY_OPENS=("play", "edit")):
            self.assertEqual(new_file.offered_extensions(self.owner), [])
        with self.settings(VAULT_FILE_EDITS=False):
            self.assertEqual(new_file.offered_extensions(self.owner), [])
        with self.settings(VAULT_STORAGE_ONLY=False, VAULT_STORAGE_ONLY_OPENS=()):
            self.assertIn(".md", new_file.offered_extensions(self.owner))

    def test_an_unmounted_editor_offers_nothing(self):
        from django.urls import NoReverseMatch

        editor = _Editor("markdown", (".md",))
        editor.get_editor_url = mock.Mock(side_effect=NoReverseMatch("no page"))
        with _registered({"markdown": editor}):
            self.assertEqual(new_file.offered_extensions(self.owner), [])


class DoorTests(_Base):
    def test_each_offered_ending_makes_its_file(self):
        for name, file_type, content in (
                ("notes.md", "markdown", b""),
                ("notes.markdown", "markdown", b""),
                ("deck.pxml", "pxml", b'<presentation title="deck.pxml"/>\n'),
                ("data.json", "json", b""),
                ("budget.sheet.json", "json", b""),
                ("plain.txt", "text", b""),
                ("script.js", "text", b""),
                ("UPPER.MD", "markdown", b"")):
            with self.subTest(name=name):
                response = self.make(name)
                self.assertEqual(response.status_code, 201, response.content)
                answer = response.json()
                self.assertEqual(answer["status"], "ok")
                self.assertEqual(answer["title"], name)
                self.assertEqual(answer["file_type"], file_type)
                made = VaultFile.objects.get(pk=answer["file_pk"])
                self.assertEqual((made.title, made.file_type), (name, file_type))
                self.assertEqual(made.owner, self.owner)
                self.assertEqual(made.bucket, self.bucket)
                self.assertEqual(made.directory, self.directory)
                self.assertFalse(made.is_public)
                self.assertEqual(self.content(made), content)
                self.assertEqual(made.file_size_bytes, len(content))
                self.assertTrue(made.key)
                # The row door draws it: what the page asks next.
                row = self.client.get(reverse("vault:file_row", args=[made.pk]))
                self.assertEqual(row.status_code, 200)
                self.assertEqual(row.json()["item"]["title"], name)
                self.assertEqual(row.json()["item"]["pid"], self.directory.pk)

    def test_without_a_folder_it_lands_at_the_top_of_ones_own_bucket(self):
        response = self.make("top.md", directory=None)
        self.assertEqual(response.status_code, 201, response.content)
        made = VaultFile.objects.get(pk=response.json()["file_pk"])
        self.assertIsNone(made.directory)
        self.assertEqual(made.bucket.owner, self.owner)
        self.assertEqual(made.bucket.slug, f"personal-{self.owner.username}")

    def test_a_missing_extension(self):
        for name in ("notes", "README"):
            with self.subTest(name=name):
                response = self.make(name)
                self.assertEqual(response.status_code, 400)
                self.assertIn("extension", response.json()["error"])
        self.assertEqual(VaultFile.objects.count(), 0)

    def test_an_unsupported_extension_names_the_ones_on_offer(self):
        for name in ("paper.pdf", "budget.uson", "photo.png", "drawing.svg",
                     "a.docx", "archive.zip", "notes.md.exe", "x.sheet"):
            with self.subTest(name=name):
                response = self.make(name)
                self.assertEqual(response.status_code, 400)
                error = response.json()["error"]
                self.assertIn("cannot be made here", error)
                self.assertIn(".md", error)
                self.assertIn(".sheet.json", error)
        self.assertEqual(VaultFile.objects.count(), 0)

    def test_names_that_are_not_one_name(self):
        for name in ("../escape.md", "a/b.md", "a\\b.md", "..md", "a..b.md",
                     " lead.md", "trail.md ", "new\nline.md", "nul\x00.md",
                     ".md", "/abs.md", "", "x" * 300 + ".md"):
            with self.subTest(name=name):
                response = self.make(name)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertTrue(response.json()["error"])
        self.assertEqual(VaultFile.objects.count(), 0)

    def test_a_duplicate_is_refused_and_the_first_is_untouched(self):
        deck = _editors()
        with _registered(deck):
            first = self.make("deck.pxml")
            self.assertEqual(first.status_code, 201)
            original = VaultFile.objects.get(pk=first.json()["file_pk"])
            before = (self.content(original), original.content_hash, original.key)
            deck["pxml"].content = "something else"
            for name in ("deck.pxml", "DECK.PXML"):
                with self.subTest(name=name):
                    again = self.make(name)
                    self.assertEqual(again.status_code, 409)
                    self.assertIn("already in this folder", again.json()["error"])
            self.assertEqual(VaultFile.objects.count(), 1)
            original.refresh_from_db()
            self.assertEqual(
                (self.content(original), original.content_hash, original.key), before)
            # The same name in another folder is another file.
            other = VaultDirectory.objects.create(
                bucket=self.bucket, name="other", owner=self.owner)
            self.assertEqual(self.make("deck.pxml", directory=other.pk).status_code, 201)
            self.assertEqual(self.make("deck.pxml", directory=None).status_code, 201)
            self.assertEqual(self.make("deck.pxml", directory=None).status_code, 409)

    def test_a_reader_cannot_make_a_file_in_anothers_folder(self):
        self.client.force_login(self.reader)
        response = self.make("mine-now.md")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(VaultFile.objects.count(), 0)
        # Nor in a folder that is theirs in a bucket that is not.
        lent = VaultDirectory.objects.create(
            bucket=self.bucket, name="lent", owner=self.reader)
        self.assertEqual(self.make("a.md", directory=lent.pk).status_code, 404)
        for missing in ("999999", "abc", "-1", "99999999999999999999999"):
            with self.subTest(directory=missing):
                self.assertEqual(self.make("a.md", directory=missing).status_code, 404)
        self.assertEqual(VaultFile.objects.count(), 0)

    def test_nobody_signed_in(self):
        self.client.logout()
        # 401 from the door; 302 where the host's login gate answers first.
        self.assertIn(self.make("a.md").status_code, (401, 302))
        self.assertEqual(VaultFile.objects.count(), 0)

    def test_get_is_not_a_door(self):
        self.assertEqual(self.client.get(reverse("vault:new_file")).status_code, 405)

    def test_a_bucket_kept_to_clearances_is_missing_for_its_owner_too(self):
        from toto.socialhub.models import Clearance
        from toto.vault.models import BucketClearance

        kept = Clearance.objects.create(name="internal", slug="internal")
        BucketClearance.objects.create(bucket=self.bucket, clearance=kept)
        self.assertEqual(self.make("a.md").status_code, 404)
        self.assertEqual(VaultFile.objects.count(), 0)

    def test_a_plan_without_the_editor_is_told_so(self):
        with _registered(_editors(only="somebody-else")):
            for name in ("a.md", "a.pdf", ""):
                with self.subTest(name=name):
                    response = self.make(name)
                    self.assertEqual(response.status_code, 402)
                    self.assertEqual(response.json()["reason"], "subscription-required")
                    self.assertIn("plan", response.json()["error"])
        # One editor open, another closed: the closed ending is the plan's.
        editors = _editors()
        editors["pxml"].only = "somebody-else"
        with _registered(editors):
            self.assertEqual(self.make("a.md").status_code, 201)
            self.assertEqual(self.make("a.pxml").status_code, 402)
        self.assertEqual(VaultFile.objects.count(), 1)

    def test_the_host_switches(self):
        with self.settings(VAULT_STORAGE_ONLY_OPENS=("play", "edit")):
            self.assertEqual(self.make("a.md").status_code, 404)
        with self.settings(VAULT_STORAGE_ONLY_OPENS=()):
            self.assertEqual(self.make("a.md").status_code, 404)
        with self.settings(VAULT_FILE_EDITS=False):
            self.assertEqual(self.make("a.md").status_code, 403)
        self.assertEqual(VaultFile.objects.count(), 0)
        with self.settings(VAULT_STORAGE_ONLY=False, VAULT_STORAGE_ONLY_OPENS=()):
            self.assertEqual(self.make("a.md").status_code, 201)

    def test_a_refused_type_and_a_bucket_that_takes_nothing(self):
        with self.settings(VAULT_REFUSED_FILE_TYPES={"markdown"}):
            self.assertEqual(self.make("a.md").status_code, 400)
        Bucket.objects.filter(pk=self.bucket.pk).update(
            storage_backend="s3", storage_config={"bucket_name": "b"})
        self.assertEqual(self.make("a.md").status_code, 403)
        self.assertEqual(VaultFile.objects.count(), 0)

    def test_a_spent_quota_refuses_before_anything_is_written(self):
        from toto.quota import QuotaExceeded
        from toto.vault.models import VaultQuotaPolicy

        policy = VaultQuotaPolicy(metric_code="storage.request", name="Requests")
        with mock.patch("toto.quota.check_quota",
                        side_effect=QuotaExceeded(policy, Decimal(5), Decimal(5))) as asked:
            response = self.make("a.md")
        self.assertEqual(response.status_code, 429)
        self.assertIn("quota exceeded", response.json()["error"])
        self.assertEqual(asked.call_args_list[0].args[1:], ("storage.request", 1, self.owner))
        self.assertEqual(VaultFile.objects.count(), 0)
        self.assertEqual(VaultUsageEvent.objects.count(), 0)

    def test_no_funds_refuses_with_402(self):
        from toto.quota.charge import InsufficientFunds

        class _Broke(InsufficientFunds):
            def __init__(self):
                Exception.__init__(self, "not enough mana")

            def __str__(self):
                return "not enough mana"

        with mock.patch("toto.quota.charge.check_funds", side_effect=_Broke()):
            response = self.make("a.md")
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["error"], "not enough mana")
        self.assertEqual(VaultFile.objects.count(), 0)

    def test_it_is_metered_and_charged_as_an_upload_is(self):
        with mock.patch("toto.quota.charge.charge") as charged:
            empty = self.make("a.md").json()["file_pk"]
            deck = self.make("deck.pxml").json()["file_pk"]
        events = {(e.metric_code, e.source_id): e for e in VaultUsageEvent.objects.all()}
        # One request each; megabytes only for the file that has bytes.
        self.assertEqual(sorted(events), [
            ("storage.request", str(empty)), ("storage.request", str(deck)),
            ("storage.transfer_mb", str(deck))])
        self.assertEqual(events[("storage.request", str(deck))].quantity, 1)
        self.assertEqual(events[("storage.request", str(deck))].user, self.owner)
        calls = [(call.args[2], call.kwargs.get("source_id")) for call in charged.call_args_list]
        self.assertEqual(calls, [
            ("storage.request", str(empty)), ("storage.request", str(deck)),
            ("storage.transfer_mb", str(deck))])

    def test_the_audit_trail_names_the_new_file(self):
        from django.apps import apps

        if not apps.is_installed("toto.audit"):
            self.skipTest("this host keeps no audit trail")
        from toto.audit.models import AuditRecord

        pk = self.make("a.md").json()["file_pk"]
        records = AuditRecord.objects.filter(action="FILE_CREATED", object_id=str(pk))
        self.assertEqual(records.count(), 1)
        record = records.get()
        self.assertEqual(record.actor_user_id, self.owner.pk)
        self.assertTrue(record.success)
        self.assertEqual(record.metadata["file_type"], "markdown")
        self.assertNotIn("a.md", str(record.metadata))


class PageTests(_Base):
    def page(self, user=None):
        self.client.force_login(user or self.owner)
        response = self.client.get(reverse("vault:public_list"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_button_and_the_modal_for_a_member_who_may(self):
        response = self.page()
        self.assertIn(".md", response.context["vault_new_extensions"])
        body = response.content.decode()
        # The toolbar's and the folder row's.
        self.assertEqual(body.count('data-vault-control="new-file"'), 2)
        self.assertIn('@click.stop="openNewFile(item)"', body)
        self.assertIn('data-vault-control="new-file-modal"', body)
        self.assertIn(f'data-new-file-url="{reverse("vault:new_file")}"', body)
        self.assertIn('x-model="newFileName"', body)
        self.assertIn('data-vault-control="new-file-error"', body)
        self.assertIn('id="vault-new-extensions"', body)
        # One field: the old menu of type pills is not drawn with it.
        self.assertNotIn('@click.stop="openCreateFile(item)"', body)
        self.assertNotIn("setCreateType(key)", body)
        [folder] = [i for i in response.context["flat_items"] if i["t"] == "dir"]
        self.assertTrue(folder["can_create"])

    def test_the_pages_sentences_are_the_doors(self):
        body = self.page().content.decode()
        for name in ("", "a/b.md", "x" * 200 + ".md"):
            with self.subTest(name=name):
                self.assertIn(str(new_file.name_refusal(name)).replace('"', "&quot;"), body)
        self.assertIn("This file type cannot be made here. Use one of:", body)
        self.assertIn("Give the name an extension, for example notes.md.", body)
        self.assertIn("name.length > 120", body)

    def test_no_button_for_a_plan_without_the_editor(self):
        with _registered(_editors(only="somebody-else")):
            response = self.page()
            self.assertEqual(response.context["vault_new_extensions"], [])
            body = response.content.decode()
            self.assertNotIn('data-vault-control="new-file"', body)
            self.assertNotIn('data-vault-control="new-file-modal"', body)

    def test_no_button_where_the_host_names_no_new(self):
        with self.settings(VAULT_STORAGE_ONLY_OPENS=("play", "edit")):
            body = self.page().content.decode()
            self.assertNotIn('data-vault-control="new-file"', body)
            self.assertNotIn('data-vault-control="new-file-modal"', body)

    def test_a_reader_gets_no_row_button_in_anothers_folder(self):
        VaultFile.objects.create(
            owner=self.owner, title="open.md", key="new-file-open", file_type="markdown",
            bucket=self.bucket, directory=self.directory, is_public=True)
        response = self.page(self.reader)
        for item in response.context["flat_items"]:
            if item["t"] == "dir":
                self.assertFalse(item["can_create"])
