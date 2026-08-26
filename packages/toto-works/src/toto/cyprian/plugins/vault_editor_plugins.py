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


@VaultEditorPlugin.plugin(key="document", title="Document", order=28)
class DocumentEditorPlugin(VaultEditorPlugin):
    file_type = "document"

    #: `.xml`, not an extension of its own. `_EXT_MAP` pairs the two on purpose:
    #: a deck is `.pxml` and everything else XML-shaped — documents, notebooks,
    #: contracts — stays `.xml`, told apart by `file_type` rather than by name.
    new_file_extension = ".xml"

    def blank_content(self, title: str) -> str:
        """An empty v3 document, the same one `bridge.open_document` mints."""
        from toto.cyprian import document_format

        base = title.rsplit(".", 1)[0] if "." in title else title
        return document_format.dumps(document_format.new_document(base or ""))

    def get_editor_url(self, vault_file) -> str:
        return reverse("cyprian:edit", args=[vault_file.pk])
