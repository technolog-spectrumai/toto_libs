"""The bridge behind editing an HTML page in the writer.

`from_html.convert` stamps the twin document with `meta["htmlview.source"]`
— the pk of the page it was made from. This bridge closes the loop: saving
the document writes the page back, so Edit-on-html really edits the html.

**The security property**, learned from the kanban bridge next door: meta is
written by whoever saves the document, so nothing here AUTHORISES from it.
`claim` aims the write-back only at a page THE SAME OWNER holds, and
`can_edit` opens for that owner alone: a forged ref can therefore only
overwrite a file its forger could already overwrite in the source editor.
"""

from __future__ import annotations

import hashlib

from toto.cyprian.bridge import DocumentBridge

SOURCE_META = "htmlview.source"


@DocumentBridge.plugin(key=SOURCE_META, title="HTML page", order=30)
class HtmlSourceBridge(DocumentBridge):
    def claim(self, vault_file, document=None):
        from toto.vault.models import VaultFile

        if document is None:
            return None
        ref = (document.meta or {}).get(SOURCE_META, "")
        if not str(ref).isdigit():
            return None
        page = VaultFile.objects.filter(pk=int(ref), file_type="html").first()
        if page is None or page.owner_id != vault_file.owner_id:
            return None
        return page

    def can_edit(self, user, owner_object) -> bool:
        """The page's owner, precisely — which `claim` already proved is the
        twin's owner too. The write_back helper refuses without this, and
        the grant hands nobody anything they lack: it is the same person."""
        return getattr(user, "pk", None) == owner_object.owner_id

    def write_back(self, document, owner_object, *, user) -> None:
        from toto.cyprian import from_html

        html = from_html.page_of(document)
        payload = html.encode("utf-8")
        with owner_object.file.open("w") as handle:
            handle.write(html)
        owner_object.content_hash = hashlib.sha256(payload).hexdigest()
        owner_object.file_size_bytes = len(payload)
        owner_object.save(update_fields=["content_hash", "file_size_bytes"])

    def return_url(self, owner_object) -> str:
        from django.urls import NoReverseMatch, reverse

        try:
            return reverse("editor:html_display", args=[owner_object.pk])
        except NoReverseMatch:
            return ""

    def return_label(self, owner_object) -> str:
        return "HTML source"
