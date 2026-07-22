from __future__ import annotations

from typing import ClassVar

from toto.core.plugin import BasePlugin


class VaultPlayPlugin(BasePlugin):
    """
    Plugin base for vault file play actions, keyed by VaultFile.file_type.

    Register one subclass per playable file type (e.g. 'video', 'audio').
    Discovery happens via autodiscover_plugins("plugins.vault_play_plugins")
    called from VaultConfig.ready().  If no plugin is registered for a given
    file_type the Play button in the vault UI is rendered disabled.
    """

    registry: ClassVar[dict[str, "VaultPlayPlugin"]] = {}

    file_type: ClassVar[str] = ""

    @classmethod
    def for_file_type(cls, file_type: str) -> "VaultPlayPlugin | None":
        return cls.registry.get(file_type)

    def get_play_url(self, vault_file) -> str:
        raise NotImplementedError


class VaultEditorPlugin(BasePlugin):
    """
    Plugin base for vault file editor actions, keyed by VaultFile.file_type.

    Register one subclass per editable file type (e.g. 'latex').
    Discovery happens via autodiscover_plugins("plugins.vault_editor_plugins")
    called from VaultConfig.ready().  The editor button is hidden when no
    plugin is registered for a given file_type.
    """

    registry: ClassVar[dict[str, "VaultEditorPlugin"]] = {}

    file_type: ClassVar[str] = ""

    @classmethod
    def for_file_type(cls, file_type: str) -> "VaultEditorPlugin | None":
        return cls.registry.get(file_type)

    def get_editor_url(self, vault_file) -> str:
        raise NotImplementedError
