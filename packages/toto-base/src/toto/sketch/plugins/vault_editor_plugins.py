from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


# The SVG editor lives in the sketch app (sketch:svg_file_display), so its vault
# editor plugin is registered HERE — only when toto.sketch is installed
# (BUILD_SKETCH=1). Registering it from toto.editor instead would advertise an
# SVG editor whose URL is unmounted whenever sketch is off (e.g. BUILD_LATEX=1,
# BUILD_SKETCH=0), which NoReverseMatch-500s the whole vault file listing.
@VaultEditorPlugin.plugin(key="svg", title="SVG Editor", order=50)
class SvgEditorPlugin(VaultEditorPlugin):
    file_type = "svg"

    def get_editor_url(self, vault_file) -> str:
        return reverse("sketch:svg_file_display", args=[vault_file.pk])
