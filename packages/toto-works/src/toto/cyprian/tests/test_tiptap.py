"""The vendored TipTap closure and the import map that resolves it.

An import map is easy to get wrong in a way nothing else catches: a missing
entry is a module that 404s in a browser, after a deploy, with the editor simply
not appearing. These tests are the reason that cannot happen quietly.
"""

import json
import re

from django.test import SimpleTestCase

from toto.cyprian import tiptap


class ManifestTests(SimpleTestCase):
    def test_the_manifest_is_not_empty(self):
        self.assertTrue(tiptap.manifest(),
                        "no vendored TipTap — run scripts/fetch_tiptap.py")

    def test_every_manifest_entry_has_a_file(self):
        for spec, name in tiptap.manifest().items():
            with self.subTest(spec=spec):
                self.assertTrue((tiptap.VENDOR_DIR / name).is_file(), name)

    def test_the_entry_points_the_editor_imports_are_all_there(self):
        # If one of these is missing, tiptap_setup.js fails on its first import
        # and the whole editor never mounts.
        setup = (tiptap.VENDOR_DIR.parent.parent / "tiptap_setup.js").read_text()
        for spec in tiptap.bare_imports(setup):
            with self.subTest(spec=spec):
                self.assertIn(spec, tiptap.manifest())

    def test_the_map_resolves_every_import_in_every_vendored_file(self):
        # The closure test. A gap here is a 404 in production and nothing else
        # in this repo would notice.
        self.assertEqual(tiptap.missing_specifiers(), {})

    def test_no_vendored_file_is_an_mjs(self):
        # Production serves /static/ straight from nginx with stock mime.types,
        # which has no .mjs entry — the file would go out as
        # application/octet-stream and the browser would refuse the module.
        # Whitenoise (dev) knows .mjs, so this would break ONLY in production.
        for name in tiptap.manifest().values():
            with self.subTest(name=name):
                self.assertFalse(name.endswith(".mjs"), name)

    def test_no_vendored_file_carries_a_sourcemap_comment(self):
        # We do not vendor the .map, and the resilient storage would rather not
        # see the reference at all.
        for name in tiptap.manifest().values():
            text = (tiptap.VENDOR_DIR / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"(?m)^//# sourceMappingURL=", text))

    def test_the_pin_is_written_down(self):
        version = (tiptap.VENDOR_DIR / "VERSION.txt").read_text()
        self.assertIn("TipTap", version)
        self.assertIn("import map", version)
        self.assertTrue((tiptap.VENDOR_DIR / "LICENSE").is_file())


class ImportMapTests(SimpleTestCase):
    def test_it_is_a_valid_import_map(self):
        parsed = json.loads(tiptap.import_map_json().replace("\\u003c", "<"))
        self.assertIn("imports", parsed)
        self.assertIn("@tiptap/core", parsed["imports"])

    def test_every_url_goes_through_static(self):
        for spec, url in tiptap.import_map()["imports"].items():
            with self.subTest(spec=spec):
                self.assertTrue(url.startswith("/static/"), url)

    def test_a_less_than_sign_cannot_close_the_script_element(self):
        self.assertNotIn("<", tiptap.import_map_json())
