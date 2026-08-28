"""The vault's Edit button for a document.

`key` MUST equal `file_type`: the registry stores by key and
`VaultEditorPlugin.for_file_type()` is `registry.get(file_type)`, so a mismatch
makes the plugin silently unreachable. That equality is also why documents
needed their own file type — `key="xml"` belongs to `toto.editor` and
`BasePlugin.register` raises on a duplicate, so a document typed 'xml' could
never have won this button.

No `should_register` guard. The version this replaces called
`..surface.document_editor_shown()`, and `surface.py` left with the destination
half of the app in 758bfa6c — the reference outlived the module it named. The
question it was asking is answered elsewhere now: the writer is a Professional
entitlement, and a subscriber who does not have it gets the plans page from
`editing.door_for` rather than a button that was never drawn. Hiding the button
as well would take it away from the person who is entitled to it too, because
this registry has no idea who is asking.
"""

from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="ctml", title="CTML Document", order=28)
class CtmlEditorPlugin(VaultEditorPlugin):
    file_type = "ctml"

    #: Its own extension at last. It used to be `.xml` — shared with the ACE
    #: editor, so the name said nothing and the type had to be carried in the
    #: row alone. `_EXT_MAP` now maps `.ctml`, which is what makes an uploaded
    #: document arrive as a document instead of as generic XML.
    new_file_extension = ".ctml"

    def blank_content(self, title: str) -> str:
        """An empty v3 document, the same one `bridge.open_document` mints."""
        from toto.cyprian import ctml

        base = title.rsplit(".", 1)[0] if "." in title else title
        return ctml.dumps(ctml.new_document(base or ""))

    def get_editor_url(self, vault_file) -> str:
        return reverse("cyprian:edit", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="document", title="Document (legacy)", order=29)
class LegacyDocumentEditorPlugin(CtmlEditorPlugin):
    """The pre-CTML spelling, so rows migration 0023 could not reach still open.

    `new_file_extension` is deliberately blank: this plugin opens what exists
    and must never mint anything new under the old name. Memo's legacy
    `presentation` plugin does exactly this, for exactly this reason.
    """

    file_type = "document"
    new_file_extension = ""
