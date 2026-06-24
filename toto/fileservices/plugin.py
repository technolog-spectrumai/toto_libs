from __future__ import annotations

from typing import ClassVar

from toto.core.plugin import BasePlugin


class FileServicePlugin(BasePlugin):
    """
    Base for *file services* — background operations a user can run over a
    VaultFile (ffmpeg, ffprobe, transcription, …).

    Each subclass declares which file types it accepts and implements
    ``execute(run)`` which performs the work, writes any output VaultFiles, and
    records stdout/stderr on the run.  Discovery happens via
    ``autodiscover_plugins("plugins.file_service_plugins")`` from
    FileservicesConfig.ready().
    """

    registry: ClassVar[dict[str, "FileServicePlugin"]] = {}

    #: VaultFile.file_type values this service accepts. Empty/None = any type.
    accepted_file_types: ClassVar[list[str] | None] = None

    #: UI hints
    icon: ClassVar[str] = "fa-solid fa-wand-magic-sparkles"
    description: ClassVar[str] = ""
    args_label: ClassVar[str] = "Arguments"
    args_placeholder: ClassVar[str] = ""
    args_required: ClassVar[bool] = False

    #: When True, selecting this service redirects to a builder UI (see
    #: ``builder_url``) that collects arguments on its own page rather than
    #: running from a free-text arg string.
    builder: ClassVar[bool] = False

    #: When False, the service is hidden from the file's service menu but stays
    #: registered (e.g. for direct/workflow execution).
    listed: ClassVar[bool] = True

    def builder_url(self, vault_file) -> str | None:
        """Redirect target for builder services. Override in subclasses."""
        return None

    def accepts(self, vault_file) -> bool:
        if vault_file.is_encrypted:
            return False
        if not self.accepted_file_types:
            return True
        return vault_file.file_type in self.accepted_file_types

    @classmethod
    def for_file(cls, vault_file) -> list["FileServicePlugin"]:
        return [p for p in cls.all() if p.listed and p.accepts(vault_file)]

    def to_dict(self) -> dict:
        return {
            "key": self.get_key(),
            "title": self.get_title(),
            "icon": self.icon,
            "description": self.description,
            "args_label": self.args_label,
            "args_placeholder": self.args_placeholder,
            "args_required": self.args_required,
            "builder": self.builder,
        }

    # ------------------------------------------------------------------
    # Subclasses implement this.
    # ------------------------------------------------------------------
    def execute(self, run) -> list[int]:
        """
        Perform the service over ``run.input_file`` using ``run.args``.

        Must return a list of created VaultFile primary keys and may set
        ``run.stdout`` / ``run.stderr``.  Raise on failure.
        """
        raise NotImplementedError
