"""The vault's own Play (2026-10-12): a Markdown file opens in the Markdown
viewer (the owner: "in the file vault .md files will have a play button"),
a CSV file and a ``.uson`` sheet in the read-only sheet viewer, a ``.pxml``
presentation in the deck player."""

from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin


class _SheetPlay(VaultPlayPlugin):
    """A table opens in the read-only sheet viewer (2026-10-12; the owner:
    "copy univer sheets viewer - but read only - to view csv files and .uson
    files"). One class a file type: the registry is keyed by it."""

    def get_play_url(self, vault_file) -> str:
        return reverse("vault:sheet_view", args=[vault_file.pk])


@VaultPlayPlugin.plugin(key="csv", file_type="csv")
class CsvPlayPlugin(_SheetPlay):
    pass


@VaultPlayPlugin.plugin(key="uson", file_type="uson")
class UsonPlayPlugin(_SheetPlay):
    pass


class _DeckPlay(VaultPlayPlugin):
    """A presentation opens in the deck player (2026-10-12; the owner: "now
    .pxml player (presentations) - bring the code from morion")."""

    def get_play_url(self, vault_file) -> str:
        return reverse("vault:deck_view", args=[vault_file.pk])


def _register_deck_play():
    """Both spellings of the type (``pxml`` and the legacy ``presentation``),
    unless the host installs toto.memo, whose own player claims them."""
    from django.apps import apps

    if apps.is_installed("toto.memo"):
        return
    for key in ("pxml", "presentation"):
        if key not in VaultPlayPlugin.registry:
            VaultPlayPlugin.plugin(key=key, file_type=key)(type(f"DeckPlay_{key}", (_DeckPlay,), {}))


_register_deck_play()


@VaultPlayPlugin.plugin(key="markdown", file_type="markdown")
class MarkdownPlayPlugin(VaultPlayPlugin):
    def get_play_url(self, vault_file) -> str:
        return reverse("vault:markdown_view", args=[vault_file.pk])
