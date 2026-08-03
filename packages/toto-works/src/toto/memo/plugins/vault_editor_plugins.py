"""The vault's Edit button for a presentation.

`key` MUST equal `file_type`: the registry stores by key and
`VaultEditorPlugin.for_file_type()` is `registry.get(file_type)`, so a mismatch
makes the plugin silently unreachable. That equality is also why decks needed
their own file type back — `key="xml"` is taken by `toto.editor` and
`BasePlugin.register` raises on a duplicate, so a deck typed 'xml' could never
have won this button. See the comment on `VaultFile.FILE_TYPES`.
"""

from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="presentation", title="Presentation", order=30)
class PresentationEditorPlugin(VaultEditorPlugin):
    """Opens the slide editor.

    It used to open the raw XML instead, on the theory that "in the vault a
    presentation is just an editable text file". That made the vault's most
    obvious entry point the least useful one — you clicked Edit on a deck and
    got angle brackets.
    """

    file_type = "presentation"

    def get_editor_url(self, vault_file) -> str:
        return reverse("memo:edit", args=[vault_file.pk])
