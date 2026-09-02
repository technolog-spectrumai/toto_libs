from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="svg", title="Open in Sketch", order=25)
class SvgEditorPlugin(VaultEditorPlugin):
    """Deep-links an ``svg`` vault file into the sketch editor.

    This registration MUST live in the sketch app, never in ``toto.editor``:
    a plugin that reverses ``sketch:edit`` from an app that is always
    installed NoReverseMatch-500s every vault listing the moment the sketch
    app is left out of a build. The comment in
    ``toto.editor.plugins.vault_editor_plugins`` records the same lesson from
    the app's previous life. The ``key`` must equal the ``file_type``.
    """

    file_type = "svg"

    #: A drawing is a `.svg` file. Declaring the extension is what puts SVG
    #: in the vault's "New file" menu — any surface that consults the editor
    #: registry for creatable types skips a plugin without it.
    new_file_extension = ".svg"

    def blank_content(self, title: str) -> str:
        """A blank drawing board.

        `title` is deliberately unused: unlike a workbook or a deck, an SVG
        carries no name inside it, so there is nothing for the filename to
        agree with. Do not add a `<title>` element to "fix" that — it would be
        carried verbatim as prologue and mean nothing to the editor.
        """
        from toto.sketch.views import EMPTY_SVG
        return EMPTY_SVG

    def get_editor_url(self, vault_file) -> str:
        return reverse("sketch:edit", args=[vault_file.pk])
