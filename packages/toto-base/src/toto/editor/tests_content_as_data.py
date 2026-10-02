"""A file's text reaches the editor as data, never as code (2026-10-02,
crown 41).

The page wrote the file into ``editor.setValue(`{{ content|escapejs }}`)`` — a
JavaScript TEMPLATE literal, and ``escapejs`` is for a quoted string: it
leaves ``${…}`` alone, which a template literal runs. So any file holding
``${…}`` — a shell script's ``${HOME}``, a JavaScript template, a copy of
somebody else's file — ran as code when its owner opened it, or broke the
page (``${HOME}`` is a ReferenceError, and the editor never started). The text
now arrives as a ``json_script`` block.

    manage.py test toto.editor.tests_content_as_data
"""

import json
import re

from django.urls import reverse

from toto.editor.tests import EditorTestCase

SCRIPT = re.compile(r"<script(?P<attrs>\s[^>]*)?>(?P<body>.*?)</script>", re.S | re.I)


def executable_scripts(html):
    """The inline scripts the browser runs — not ``json_script`` data."""
    return [m.group("body") for m in SCRIPT.finditer(html)
            if "application/json" not in (m.group("attrs") or "")]


class ContentAsDataTests(EditorTestCase):
    TEXT = ('#!/bin/sh\necho "${HOME}" `date`\n'
            "${alert(document.domain)}\n</script><b>bold</b>\n")

    def test_the_text_is_data_and_comes_back_whole(self):
        vault_file = self._make(self.TEXT, title="run.sh")
        page = self.client.get(reverse("editor:text_display", args=[vault_file.pk]))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertEqual([body for body in executable_scripts(html)
                          if "${alert(document.domain)}" in body or "${HOME}" in body], [])
        block = re.search(r'<script id="editor-content" type="application/json">(.*?)</script>',
                          html, re.S)
        self.assertIsNotNone(block)
        self.assertEqual(json.loads(block.group(1)), self.TEXT)
