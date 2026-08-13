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

    def get_editor_url(self, vault_file) -> str:
        return reverse("sketch:edit", args=[vault_file.pk])
