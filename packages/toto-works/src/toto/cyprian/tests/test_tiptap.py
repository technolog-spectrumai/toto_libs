"""Cyprian's half of the TipTap arrangement.

The vendored files and the import map that resolves them live in **memo** — it
owns them because both editors in this wheel run on TipTap and memo is the app
cyprian is allowed to depend on. Those tests moved there with the files; see
`toto.memo.tests.TipTapVendorTests`.

What stays here is the part that is cyprian's: its own `tiptap_setup.js` must
import nothing the shared map cannot answer, and the page must actually carry
the map. A gap is a module that 404s in a browser, after a deploy, with the
writer simply not appearing.
"""

from django.test import SimpleTestCase

from toto.memo import tiptap

# VENDOR_DIR is …/memo/static/memo/vendor/tiptap; parents[4] is the src/toto
# both apps share.
SETUP = (tiptap.VENDOR_DIR.parents[4]
         / "cyprian" / "static" / "cyprian" / "tiptap_setup.js")


class SetupModuleTests(SimpleTestCase):
    def test_the_setup_module_is_where_the_template_looks_for_it(self):
        self.assertTrue(SETUP.is_file(), SETUP)

    def test_every_specifier_the_writer_imports_is_in_the_shared_map(self):
        # If one of these is missing, tiptap_setup.js fails on its first import
        # and the whole editor never mounts.
        specs = tiptap.bare_imports(SETUP.read_text(encoding="utf-8"))
        self.assertTrue(specs, "the setup module imports nothing — did it move?")
        for spec in sorted(specs):
            with self.subTest(spec=spec):
                self.assertIn(spec, tiptap.manifest())

    def test_it_imports_from_memos_vendor_directory(self):
        # The move is easy to half-undo: a stale cyprian/static/cyprian/vendor/
        # would resolve locally and ship nothing.
        self.assertEqual(tiptap.STATIC_PREFIX, "memo/vendor/tiptap/")
        self.assertFalse(
            (SETUP.parent / "vendor" / "tiptap").exists(),
            "cyprian still has its own copy of the vendored TipTap")
