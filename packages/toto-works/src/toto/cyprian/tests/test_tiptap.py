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

from toto.cyprian import tiptap

# VENDOR_DIR is …/cyprian/static/cyprian/vendor/tiptap, so the setup module is
# three levels up beside it.
SETUP = tiptap.VENDOR_DIR.parents[1] / "tiptap_setup.js"


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

    def test_it_imports_from_cyprians_own_vendor_directory(self):
        # The bundle moved out of memo in 8/2026 with the rest of the editor
        # machinery. A stale memo copy would still resolve on a dev box and
        # ship nothing, so assert the old home is really gone.
        self.assertEqual(tiptap.STATIC_PREFIX, "cyprian/vendor/tiptap/")
        self.assertTrue(tiptap.VENDOR_DIR.is_dir(), "the vendored bundle is missing")
        memo_copy = tiptap.VENDOR_DIR.parents[3] / "memo" / "static" / "memo" / "vendor"
        self.assertFalse(memo_copy.exists(), "memo still has a copy of the vendored TipTap")
