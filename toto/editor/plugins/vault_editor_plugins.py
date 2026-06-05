from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="text", title="Text Editor", order=30)
class TextEditorPlugin(VaultEditorPlugin):
    file_type = "text"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:text_display", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="json", title="JSON Editor", order=40)
class JsonEditorPlugin(VaultEditorPlugin):
    file_type = "json"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:json_display", args=[vault_file.pk])
