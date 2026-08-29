"""Migration 0024: wiki pages convert, standalone documents die.

The destructive half is deliberate — the instruction was to delete CTML data,
not to park it — and the migration's own docstring is the operator's warning.
What this suite pins is the LINE between the two populations, because getting
it wrong in either direction is the disaster: converting a standalone document
merely disobeys, deleting a wiki page's file makes the writer open blank and
the first save destroy a page's prose.

Calls the migration's own `forward` against real rows, so the thing under test
is the thing that will run — the shape `tests_pxml_migration` established.
"""
import importlib
import tempfile

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from toto.vault.models import Bucket, VaultFile

User = get_user_model()

_MEDIA = tempfile.mkdtemp(prefix="ctml-retirement-")

_MIGRATION = importlib.import_module("toto.vault.migrations.0024_retire_ctml")

CTML = (b'<?xml version="1.0"?>\n'
        b'<document version="3" title="Notes">'
        b'<content><![CDATA[<p>the prose</p>]]></content></document>\n')


@override_settings(MEDIA_ROOT=_MEDIA)
class CtmlRetirementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="pw")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="B", slug="b", storage_backend="local")

    def file(self, *, key, file_type="ctml", name="notes.ctml", body=CTML, **extra):
        row = VaultFile.objects.create(
            owner=self.user, bucket=self.bucket, title=name, key=key,
            file_type=file_type, **extra)
        row.file.save(name, ContentFile(body), save=True)
        return row

    def migrate(self):
        _MIGRATION.forward(global_apps, None)

    # -- the deletion -------------------------------------------------------

    def test_a_standalone_document_is_deleted_row_and_bytes(self):
        row = self.file(key="alone")
        storage, blob = row.file.storage, row.file.name
        self.migrate()
        self.assertFalse(VaultFile.objects.filter(pk=row.pk).exists())
        self.assertFalse(storage.exists(blob))

    def test_the_legacy_spelling_dies_with_it(self):
        row = self.file(key="old", file_type="document", name="old.xml")
        self.migrate()
        self.assertFalse(VaultFile.objects.filter(pk=row.pk).exists())

    # -- the conversion -----------------------------------------------------

    def _wiki_row(self, row):
        """Point a kanban wiki page at the file, where kanban is installed.

        Through the model rather than a fixture, because `kanban_wiki_pages`
        is the reverse relation the migration itself asks.
        """
        from django.apps import apps

        if not apps.is_installed("toto.kanban"):
            self.skipTest("built without the boards")
        from toto.kanban.models import DocumentationPage, Project
        from toto.people.models import Person

        lead = Person.objects.create(user=self.user, display_name="Owner")
        project = Project.objects.create(name="P", project_lead=lead)
        return DocumentationPage.objects.create(
            project=project, title="Page", slug="page",
            body_html="<p>the prose</p>", vault_file=row)

    def test_a_wiki_pages_file_is_converted_in_place(self):
        row = self.file(key="wiki")
        page = self._wiki_row(row)
        self.migrate()
        row.refresh_from_db()
        self.assertEqual(row.file_type, "html")
        with row.file.storage.open(row.file.name, "rb") as fh:
            body = fh.read().decode()
        self.assertIn("<p>the prose</p>", body)
        self.assertIn("<!doctype html>", body.lower())
        self.assertNotIn("<![CDATA[", body)
        page.refresh_from_db()
        self.assertEqual(page.vault_file_id, row.pk)

    # -- what it must not touch ---------------------------------------------

    def test_an_encrypted_row_is_left_alone_not_deleted(self):
        """The bytes are a sealed frame this process cannot open. Destroying a
        file we cannot read to check what it is would be the worst possible
        reading of the instruction."""
        row = self.file(key="sealed", is_encrypted=True)
        self.migrate()
        self.assertTrue(VaultFile.objects.filter(pk=row.pk).exists())

    def test_a_mirrored_stub_is_left_alone(self):
        row = self.file(key="peer", origin="mirror")
        self.migrate()
        self.assertTrue(VaultFile.objects.filter(pk=row.pk).exists())

    def test_everything_else_is_untouched(self):
        page = self.file(key="page", file_type="html", name="page.html",
                         body=b"<!doctype html><p>hi</p>")
        self.migrate()
        page.refresh_from_db()
        self.assertEqual(page.file_type, "html")
