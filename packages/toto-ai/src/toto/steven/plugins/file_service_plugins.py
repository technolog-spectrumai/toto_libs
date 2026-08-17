"""The wand entry: "ask the assistant about this whole file".

A **builder** service, and that is the whole trick. A non-builder plugin has to
implement ``execute(run)`` against a ``FileServiceRun`` — a model in
toto-media-ops, a wheel zenobia does not pin. A builder one only supplies a URL,
so this offers a file action on every host that has the vault, which is all of
them, and runs it through steven's own `AiRun` rather than a second substrate.

Registered by ``VaultConfig.ready()``'s autodiscovery, which is why the registry
had to move into the vault first.
"""

from toto.vault.plugins import FileServicePlugin


@FileServicePlugin.plugin(key="steven", title="Ask the assistant", order=5)
class StevenFileServicePlugin(FileServicePlugin):
    """Read a whole file and answer a question about it."""

    icon = "fa-solid fa-wand-magic-sparkles"
    description = ("Summarise, explain or check a whole file. The answer is "
                   "shown to you; nothing is written back on its own.")
    builder = True

    #: Text-shaped types only. A prompt over a video is a question nobody can
    #: answer from the bytes, and offering it would be a lie in a menu.
    accepted_file_types = [
        "text", "html", "json", "yaml", "xml", "csv", "latex", "bib",
        "python", "svg", "document", "neojson",
        # Decks are text too. They reached this list as "xml" before they had a
        # class of their own; "presentation" is the legacy spelling.
        "pxml", "presentation",
    ]

    def accepts(self, vault_file) -> bool:
        """Also refuse AI-protected buckets — the menu half of the shield.

        The file-ask view refuses on its own too; this just keeps a dead
        entry out of the menu.
        """
        from toto.core import assistant

        return (super().accepts(vault_file)
                and assistant.allowed_for_file(vault_file))

    def builder_url(self, vault_file) -> str:
        from django.urls import NoReverseMatch, reverse

        try:
            return reverse("steven:file_ask", args=[vault_file.pk])
        except NoReverseMatch:
            return ""
