"""Migration 0023: written documents become the `ctml` file class.

The mirror image of `tests_pxml_migration`, and it exists for the same reason:
the data half of the migration would otherwise ship untested, and the branch
that matters is the content sniff — the one that decides whether a DECK or a
NOTEBOOK gets swallowed as a document.

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

_MEDIA = tempfile.mkdtemp(prefix="ctml-migration-")

# importlib, not a plain import: the module name starts with a digit.
_MIGRATION = importlib.import_module("toto.vault.migrations.0023_ctml_file_type")

DOCUMENT = b"""<?xml version="1.0"?>
<document version="3" title="Notes"><content><![CDATA[<p>hello</p>]]></content></document>
"""
DECK = b"""<?xml version="1.0"?>
<presentation version="2" title="Q"><slide id="s-1"><title>t</title></slide></presentation>
"""
NOTEBOOK = b"""<?xml version="1.0"?>
<notebook title="Run"><cell>1 + 1</cell></notebook>
"""


@override_settings(MEDIA_ROOT=_MEDIA)
class CtmlMigrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="pw")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="B", slug="b", storage_backend="local")

    def file(self, *, key, file_type, name, body=DOCUMENT, **extra):
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

    # -- by name ------------------------------------------------------------

    def test_a_document_row_is_retyped(self):
        row = self.file(key="a", file_type="document", name="notes.xml")
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "ctml")

    def test_the_title_is_deliberately_left_alone(self):
        """0021 retitled `.xml` -> `.pxml` here. This one does not: the
        decision for CTML was to keep existing filenames, which is why
        `cyprian._adopt` survives as the repair for a re-uploaded document."""
        row = self.file(key="a", file_type="document", name="notes.xml")
        self.migrate()
        self.assertEqual(self.reread(row).title, "notes.xml")

    def test_an_encrypted_document_row_migrates_by_name(self):
        """No bytes are read for the name pass, so a sealed document is fine —
        it is only the CONTENT pass that cannot see inside one."""
        row = self.file(key="a", file_type="document", name="sealed.xml",
                        is_encrypted=True)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "ctml")

    # -- by content ---------------------------------------------------------

    def test_a_document_filed_as_xml_is_caught(self):
        """The population `_adopt` never reached: uploaded by hand, never
        opened by its owner."""
        row = self.file(key="b", file_type="xml", name="stray.xml")
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "ctml")

    def test_a_deck_is_not_swallowed(self):
        """Decks are `xml`-shaped too. Matching "is XML" rather than the ROOT
        tag would retype every one of them into a document."""
        row = self.file(key="c", file_type="xml", name="deck.xml", body=DECK)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "xml")

    def test_a_notebook_is_not_swallowed(self):
        row = self.file(key="d", file_type="xml", name="run.xml", body=NOTEBOOK)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "xml")

    def test_an_encrypted_xml_row_is_left_alone(self):
        """Permanently unidentifiable — the bytes are a sealed frame. The
        remedy is the Rename dialog's type dropdown, and it is why
        ('document', 'Document') stays in FILE_TYPES forever."""
        row = self.file(key="e", file_type="xml", name="sealed.xml",
                        is_encrypted=True)
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "xml")

    def test_a_mirrored_stub_is_left_alone(self):
        """`mirror._upsert_stub` re-applies the peer's type on every refresh,
        so retyping one here is churn that undoes itself. `mirror.py`
        normalises inbound instead."""
        row = self.file(key="f", file_type="document", name="peer.xml",
                        origin="mirror")
        self.migrate()
        self.assertEqual(self.reread(row).file_type, "document")

    def test_an_unreadable_row_does_not_abort_the_migrate(self):
        """One missing file must not roll back a migrate that has already
        committed the AlterField."""
        good = self.file(key="g", file_type="document", name="fine.xml")
        broken = self.file(key="h", file_type="xml", name="gone.xml")
        broken.file.storage.delete(broken.file.name)
        self.migrate()
        self.assertEqual(self.reread(good).file_type, "ctml")
        self.assertEqual(self.reread(broken).file_type, "xml")

    # -- reverse ------------------------------------------------------------

    def test_reverse_collapses_to_the_legacy_spelling(self):
        """Lossy on purpose: forward merges two populations and the
        distinction is not recoverable."""
        named = self.file(key="i", file_type="document", name="a.xml")
        sniffed = self.file(key="j", file_type="xml", name="b.xml")
        self.migrate()
        self.unmigrate()
        self.assertEqual(self.reread(named).file_type, "document")
        self.assertEqual(self.reread(sniffed).file_type, "document")

    # -- the frozen sniffer -------------------------------------------------

    def test_the_sniffer_reads_the_root_tag_only(self):
        looks = _MIGRATION._looks_like_a_document
        self.assertTrue(looks(DOCUMENT))
        self.assertFalse(looks(DECK))
        self.assertFalse(looks(NOTEBOOK))
        # A <document> mentioned further in is not a document.
        self.assertFalse(looks(b"<notebook><cell>&lt;document&gt;</cell></notebook>"))

    def test_the_sniffer_skips_a_prolog_and_comments(self):
        looks = _MIGRATION._looks_like_a_document
        self.assertTrue(looks(b'<?xml version="1.0"?>\n<!-- note -->\n<document/>'))
