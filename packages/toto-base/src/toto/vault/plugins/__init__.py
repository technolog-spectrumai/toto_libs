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

    def is_open_to(self, user) -> bool:
        """Whether this member is offered the button at all (2026-10-06).

        The default, True, is every plugin as it was. A plugin whose page a
        plan sells answers False for a member whose plan lacks it, and the
        listing then draws no Play for them: a button that only ever opens
        the plans page is worse than none. Asked by the listing for the
        member it is built for, once for every row, so the answer has to be
        cheap. The page behind the button still decides for itself."""
        return True

    def get_play_url(self, vault_file) -> str:
        raise NotImplementedError


def open_to(plugin, user) -> bool:
    """A Play or Edit plugin's :meth:`is_open_to`, asked safely.

    ``user`` is ``None`` where a row is built for nobody in particular, and
    then nothing is asked. A plugin that raises closes: a broken answer
    must not put a paid button on a page, nor take the listing down."""
    if user is None:
        return True
    try:
        return bool(plugin.is_open_to(user))
    except Exception:  # noqa: BLE001 - a broken plugin shows no button
        return False


class _UnsavedProbe:
    """Stands in for a VaultFile when asking a plugin to build a URL.

    `pk` is None, so `reverse(..., args=[None])` raises NoReverseMatch for the
    same reason a missing route does — which is why `is_available` treats only
    that exception as decisive and everything else as "available".
    """

    pk = 0
    id = 0
    file_type = ""
    title = ""


_UNSAVED_PROBE = _UnsavedProbe()


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

    #: The extension a brand-new file of this type gets — ".pxml", ".json".
    #: Blank means the vault's "New file" menu does not offer this type, which
    #: is the default and stays right for anything the vault can already seed
    #: from `CreateEmptyFileView._INITIAL`.
    #:
    #: This exists because of the rule the whole plugin system is built on: the
    #: vault must not import the apps above it. "What does an empty spreadsheet
    #: look like" is a question only `toto.primula` can answer — a workbook is
    #: not an empty file, and `is_sheet()` refuses one — so `CREATABLE_TYPES`
    #: excluded every rich type and its comment said as much. Asking the plugin
    #: inverts that the same way `VaultAccessPlugin` inverts "who may write".
    new_file_extension: ClassVar[str] = ""

    #: The endings a file made by the vault's one-field "New" may be named
    #: with for this type (stage 62b) — ``(".md", ".markdown")``, or a longer
    #: one where a name has to say more than its last part does
    #: (``".sheet.json"``). Empty, the default, offers nothing. Separate from
    #: ``new_file_extension`` above, which feeds the old menu of type pills:
    #: that one is closed on a storage-only host whatever it says.
    new_file_extensions: ClassVar[tuple] = ()

    @classmethod
    def for_file_type(cls, file_type: str) -> "VaultEditorPlugin | None":
        plugin = cls.registry.get(file_type)
        if plugin is not None and not plugin.is_available():
            return None
        return plugin

    def is_available(self) -> bool:
        """Whether this editor can actually be reached on THIS host.

        Installed is not the same as mounted. A host may install an app for
        half of what it does and register only some of its urlconf — zenobia
        mounts `toto.memo.reader_urls`, so decks are shown and never authored
        — and then the app's plugins still register from `ready()` while
        `get_editor_url` reverses a route that does not exist. That is a 500
        on an Edit link and a "New file" menu offering a type nobody can save.

        The default asks the urlconf, which is the honest question: a plugin
        whose editor URL cannot be reversed is not available. Overriding this
        is for a plugin whose availability turns on something else.
        """
        from django.urls import NoReverseMatch

        try:
            self.get_editor_url(_UNSAVED_PROBE)
        except NoReverseMatch:
            return False
        except Exception:  # noqa: BLE001 - a probe must never break a listing
            # Anything else means the URL does not depend on a route that is
            # missing — most plugins interpolate the file's pk and would raise
            # on the probe object. Available.
            return True
        return True

    def is_open_to(self, user) -> bool:
        """Whether this member is offered the editor at all (2026-10-06):
        :meth:`VaultPlayPlugin.is_open_to`'s rule, for Edit. The default,
        True, is every plugin as it was."""
        return True

    def get_editor_url(self, vault_file) -> str:
        raise NotImplementedError

    def blank_content(self, title: str) -> str:
        """The text a brand-new file of this type starts with.

        Only ever called for a plugin that declares `new_file_extension`, and it
        must return something the app's own reader accepts: the file is created
        and the user is redirected straight into the editor, so a blank that
        does not parse is a new document that opens broken.

        ``title`` is the filename the user chose, so a format with a name inside
        it — a workbook, a deck — can agree with the file it lives in.
        """
        return ""


    def new_file_content(self, name: str) -> str:
        """What a file called ``name``, made by the one-field "New", starts
        with (stage 62b). Asked only for a name that ends in one of
        ``new_file_extensions``, and it has to be something this plugin's
        own editor opens: a deck or a workbook is never an empty file."""
        return ""


class VaultAccessPlugin(BasePlugin):
    """Who, besides the owner, may WRITE a file of this type.

    The vault decides access from what it can see on the row: owner, staff, a
    directory ACL, or public. That is the whole truth for a file the vault
    owns outright — and it is wrong for a file some other app lends to a team.
    A cyprian project-wiki page is held by the project lead and written by the
    whole team, and the vault cannot know that: the rule lives in
    ``toto.cyprian``'s DocumentBridge, in a package this one must not import.

    So the question is inverted. An app that lends its files out registers one
    plugin here, keyed by file_type, and the vault asks it. Without this, a
    wiki collaborator could open the writer and save (cyprian's own gate lets
    them) but could not claim the editing lock or see the version history,
    because those endpoints only ever asked the vault — which said no.

    Deliberately a registry of its own rather than a method on
    ``VaultEditorPlugin``: that one is gated on whether a host SHOWS the editor
    (``should_register`` → ``document_editor_shown()``), and hiding a dashboard
    tile must never quietly withdraw a team's locks. Access is not visibility.
    """

    registry: ClassVar[dict[str, "VaultAccessPlugin"]] = {}

    file_type: ClassVar[str] = ""

    @classmethod
    def for_file_type(cls, file_type: str) -> "VaultAccessPlugin | None":
        return cls.registry.get(file_type)

    def may_edit(self, user, vault_file) -> bool:
        """True when this user may write the file despite not owning it.

        Answer only for the rights this plugin's app grants. Never widen: the
        vault's own checks have already run and said no.
        """
        raise NotImplementedError


class FileServicePlugin(BasePlugin):
    """A background operation somebody can run over a VaultFile.

    **Moved here from `toto.fileservices` in 1.51, and the move is the point.**
    The class is 78 lines with no media imports at all, but it lived in
    **toto-media-ops**, which at the time only the placidia host pinned (zenobia
    pins it again since 1.50, but the argument does not depend on that — a wheel
    ANY host may decline is a wheel the vault cannot import). So the vault, which
    owns every file, could not name the registry describing what may be done to
    one:
    `vault/views.py` reached for it inside a bare `try/except`, and on zenobia —
    the host with all six editors — the whole "run something over this file"
    affordance silently did not exist.

    The registry belongs with the files. The RUN SUBSTRATE does not, and stays
    where it is: `FileServiceRun`, its dispatch and its runner are ffmpeg-shaped
    and remain in toto-media-ops. That split is why `builder` matters — a
    builder plugin never touches the run model, it redirects to its own page,
    so an app in any wheel can offer a file action on any host.
    """

    """
    Base for *file services* — background operations a user can run over a
    VaultFile (ffmpeg, ffprobe, …).

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
        # Non-local content: a service run reads bytes it expects on this
        # disk. Download crosses the wire; a service does not.
        from toto.vault import access

        if not access.is_local_content(vault_file):
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
