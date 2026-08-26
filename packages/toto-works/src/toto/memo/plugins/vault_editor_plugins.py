"""The vault's Edit button for a deck.

`key` MUST equal `file_type`: the registry stores by key and
`VaultEditorPlugin.for_file_type()` is `registry.get(file_type)`, so a mismatch
makes the plugin silently unreachable.

**Both spellings are claimed, and that is not belt-and-braces.** Migration 0021
renamed the deck class from `presentation` to `pxml`, and it could not reach
mirrored stubs, s3-backed rows or encrypted decks — `views.DECK_TYPES` carries
both for exactly that reason. A deck the migration missed would otherwise read
fine in the presenter and have no way in to the editor, which reads as the
editor being broken rather than as a file that was never retyped.

Two classes rather than one with two keys, because the registry is keyed by a
single string per plugin.
"""

from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="pxml", title="Presentation", order=30)
class PresentationEditorPlugin(VaultEditorPlugin):
    file_type = "pxml"

    #: `.pxml`, which is what makes a deck identifiable by NAME. The first deck
    #: era typed them `presentation` and named them `.xml`, so every listing
    #: sniffed up to 300 files off disk to tell them apart; vault migration 0021
    #: and this extension replaced that. A new deck must not reintroduce it.
    new_file_extension = ".pxml"

    def blank_content(self, title: str) -> str:
        """One empty slide, so the deck opens and presents rather than erroring."""
        from toto.memo import presentation_format

        base = title.rsplit(".", 1)[0] if "." in title else title
        return presentation_format.dumps(
            presentation_format.new_presentation(title=base or "Presentation"))

    def get_editor_url(self, vault_file) -> str:
        return reverse("memo:edit", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="presentation", title="Presentation", order=31)
class LegacyPresentationEditorPlugin(PresentationEditorPlugin):
    """The pre-0021 spelling. See the module docstring."""

    file_type = "presentation"

    #: Blank, overriding the parent: this spelling exists so decks 0021 could
    #: not reach still have a way in to the editor. Offering it in the vault's
    #: "New file" menu would mint NEW decks under the legacy name and undo the
    #: rename one file at a time.
    new_file_extension = ""
