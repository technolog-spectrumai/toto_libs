"""Migration 0021: decks become the `pxml` file class.

The data half of 0021 would otherwise ship untested. On this host there are no
`file_type='xml'` rows at all, so the content-sniffing branch passes by vacuity
— and it is the branch that decides whether a cyprian document or a notebook
gets swallowed as a deck.

These call the migration's own `forward`/`backward` against real rows rather
than re-implementing them, so the thing under test is the thing that will run.
"""
import importlib
import tempfile

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from toto.vault.models import Bucket, VaultFile

User = get_user_model()

_MEDIA = tempfile.mkdtemp(prefix="pxml-migration-")

# importlib, not a plain import: the module name starts with a digit.
_MIGRATION = importlib.import_module("toto.vault.migrations.0021_pxml_file_type")

DECK = b"""<?xml version="1.0"?>
<presentation version="2" title="Q"><slide id="s-1"><title>t</title></slide></presentation>
"""
DOCUMENT = b"""<?xml version="1.0"?>
<document title="Notes"><page><p>not a deck</p></page></document>
"""
NOTEBOOK = b"""<?xml version="1.0"?>
<notebook title="Run"><cell>1 + 1</cell></notebook>
"""


@override_settings(MEDIA_ROOT=_MEDIA)
class PxmlMigrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="pw")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="B", slug="b", storage_backend="local")

    def file(self, *, key, file_type, name, body=DECK, **extra):
        row = VaultFile.objects.create(
            owner=self.user, bucket=self.bucket, title=name, key=key,
            file_type=file_type, **extra)
        row.file.save(name, ContentFile(body), save=True)
        return row

    def migrate(self):
        _MIGRATION.forward(global_apps, None)

    def unmigrate(self):
        _MIGRATION.backward(global_apps, None)

    def reread(self, row):
        row.refresh_from_db()
        return row

    # ── retyping ─────────────────────────────────────────────────────────────

    def test_a_legacy_typed_deck_becomes_pxml(self):
        row = self.file(key="a", file_type="presentation", name="talk.xml")
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "pxml")

    def test_an_xml_row_holding_deck_bytes_becomes_pxml(self):
        # The one non-repeating chance to catch these: memo's sniff is gone.
        row = self.file(key="b", file_type="xml", name="talk.xml")
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "pxml")

    def test_a_cyprian_document_is_not_swallowed(self):
        # Documents are `xml` too. Matching "is XML" rather than the root tag
        # would retype every one of them into a deck.
        row = self.file(key="c", file_type="xml", name="notes.xml", body=DOCUMENT)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "xml")

    def test_a_notebook_is_not_swallowed(self):
        row = self.file(key="d", file_type="xml", name="run.xml", body=NOTEBOOK)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "xml")

    # ── the title, which is what the API serves as a filename ────────────────

    def test_the_title_suffix_follows_the_type(self):
        # FileDownloadApiView serves `title`. Without this, downloading a
        # migrated deck and uploading it back types it straight to xml again —
        # and there is no sniff left to rescue it.
        row = self.file(key="e", file_type="presentation", name="talk.xml")
        self.migrate()
        self.assertEqual(self.reread(row).title, "talk.pxml")

    def test_a_title_that_is_not_dot_xml_is_left_alone(self):
        row = self.file(key="f", file_type="presentation", name="Quarterly review")
        self.migrate()
        self.assertEqual(self.reread(row).title, "Quarterly review")

    def test_the_key_never_moves(self):
        # `key` is the vault's identity and is unique per bucket; rewriting it
        # would break every link and could collide.
        row = self.file(key="g", file_type="presentation", name="talk.xml")
        self.migrate()
        self.assertEqual(self.reread(row).key, "g")

    # ── what it deliberately does not touch ──────────────────────────────────

    def test_an_encrypted_xml_deck_is_left_alone(self):
        # Sealed bytes cannot be sniffed. Documented as unrecoverable, with the
        # rename dropdown as the manual remedy.
        row = self.file(key="h", file_type="xml", name="secret.xml",
                        body=b"TOTOSEAL\x01sealed", is_encrypted=True)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "xml")

    def test_an_encrypted_row_already_typed_as_a_deck_still_migrates(self):
        # By NAME, so no bytes are read and encryption is irrelevant.
        row = self.file(key="i", file_type="presentation", name="secret.xml",
                        body=b"TOTOSEAL\x01sealed", is_encrypted=True)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "pxml")

    def test_a_mirrored_stub_is_left_alone(self):
        # The peer re-stamps its file_type on every refresh, so retyping a stub
        # is churn that undoes itself; mirror.py normalises inbound instead.
        row = self.file(key="j", file_type="presentation", name="talk.xml",
                        origin="mirror")
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "presentation")

    def test_a_row_in_a_non_local_bucket_is_left_alone(self):
        remote = Bucket.objects.create(
            owner=self.user, name="R", slug="r", storage_backend="s3")
        row = VaultFile.objects.create(
            owner=self.user, bucket=remote, title="talk.xml", key="k",
            file_type="xml")
        row.file.save("talk.xml", ContentFile(DECK), save=True)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "xml")

    def test_a_missing_file_does_not_abort_the_migration(self):
        # One unreadable row must not take down a migrate that has already
        # committed the schema change.
        broken = self.file(key="l", file_type="xml", name="gone.xml")
        broken.file.storage.delete(broken.file.name)
        good = self.file(key="m", file_type="presentation", name="talk.xml")
        self.migrate()
        self.assertEqual(self.reread(good).file_type, "pxml")

    # ── reverse ──────────────────────────────────────────────────────────────

    def test_it_goes_backwards(self):
        row = self.file(key="n", file_type="presentation", name="talk.xml")
        self.migrate()
        self.unmigrate()
        row = self.reread(row)
        self.assertEqual(row.file_type, "presentation")
        self.assertEqual(row.title, "talk.xml")

    def test_the_reverse_is_lossy_and_that_is_intended(self):
        # Both populations collapse to `presentation` — which is exactly where
        # pre-0021 memo would have converged them anyway.
        was_xml = self.file(key="o", file_type="xml", name="a.xml")
        was_typed = self.file(key="p", file_type="presentation", name="b.xml")
        self.migrate()
        self.unmigrate()
        self.assertEqual(self.reread(was_xml).file_type, "presentation")
        self.assertEqual(self.reread(was_typed).file_type, "presentation")
