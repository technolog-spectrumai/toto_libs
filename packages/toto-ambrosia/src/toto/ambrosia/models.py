"""The workspace: a folder inside a vault bucket, and nothing that runs.

A workspace IS a folder inside a vault bucket. Ambrosia owns no file storage of
its own — every file in a workspace is an ordinary `vault.VaultFile`, so it is
visible in the vault browser, counts against the same quota, and can be moved,
downloaded or encrypted with the tools that already exist.

Since the 1.46 split ambrosia is the BASE only: the workspace framing, the
editor, the tree and the file CRUD. What runs lives in the language apps —
`toto.dracena` (the kernel session, run in a Compute Gear) and `toto.texlab`
(the compiler, `LatexRun`) — which register themselves in `registry.py`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from toto.vault.storage import private_storage as _private_storage
from django.utils import timezone
from django.utils.text import slugify


class WorkspaceKind(models.TextChoices):
    PYTHON = "python", "Python"
    LATEX = "latex", "LaTeX"


class ExecutionMode(models.TextChoices):
    # A persistent Jupyter kernel: variables survive between runs, and figures
    # come back as rich output.
    KERNEL = "kernel", "Interactive kernel"
    # One subprocess per run through Celery. No persistent state. Wired up in the
    # run-history part; the field exists now so the choice is recorded per
    # workspace rather than bolted on globally later.
    CELERY = "celery", "Batch (Celery)"


class AmbrosiaSettings(models.Model):
    """Singleton configuration for the workspace base, editable in the admin.

    A Django setting would have been one line, and wrong for this: how many
    workspaces a person may hold is an operator's decision about THIS
    deployment, and an operator should be able to change it without a redeploy.
    So it lives in a row, the way toto.weather keeps its providers.

    Why a count rather than a quota metric: `toto.quota` meters ACTIONS over a
    period and its usage events only ever accumulate, so a workspace that was
    deleted would go on occupying its slot forever. This is a cap on what you
    currently HOLD — destroy a workspace and the slot comes back — which is a
    live count, not a consumption total.
    """

    max_workspaces_per_user = models.PositiveIntegerField(
        default=5,
        verbose_name="Maximum workspaces per user",
        help_text=(
            "How many workspaces one person may hold at a time, across every "
            "lab. Destroying a workspace frees its slot immediately. "
            "0 means no limit."
        ),
    )

    class Meta:
        verbose_name = "Ambrosia Settings"
        verbose_name_plural = "Ambrosia Settings"

    def __str__(self):
        cap = self.max_workspaces_per_user
        return f"Ambrosia Settings (max {cap} per user)" if cap else "Ambrosia Settings (no limit)"

    @classmethod
    def get(cls) -> "AmbrosiaSettings":
        """The one row, created on first read so a fresh install has defaults."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def workspace_cap(cls) -> int:
        """The cap, or 0 for none. Never raises: a settings row that cannot be
        read must not be the reason nobody can make a workspace, so a database
        that is mid-migration falls back to the field's own default."""
        try:
            return cls.get().max_workspaces_per_user
        except Exception:  # noqa: BLE001 — the field default is the safe answer
            return cls._meta.get_field("max_workspaces_per_user").default


class Workspace(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="ambrosia_workspaces",
    )
    kind = models.CharField(
        max_length=16, choices=WorkspaceKind.choices, default=WorkspaceKind.PYTHON)
    execution = models.CharField(
        max_length=16, choices=ExecutionMode.choices, default=ExecutionMode.KERNEL)

    # A workspace is a DIRECTORY inside an existing bucket, not a bucket of its
    # own. One bucket can therefore hold many workspaces side by side, which is
    # why `root_directory` is the real identity and everything — the tree, file
    # creation, destruction — is scoped to it rather than to the bucket.
    #
    # Both cascade: a workspace whose folder has been deleted in the vault is not
    # a workspace with a missing folder, it is nothing at all. PROTECT here would
    # only mean vault refusing to delete a directory for reasons it cannot
    # explain to the person clicking delete.
    bucket = models.ForeignKey(
        "vault.Bucket", on_delete=models.CASCADE, related_name="ambrosia_workspaces")
    root_directory = models.OneToOneField(
        "vault.VaultDirectory", on_delete=models.CASCADE,
        related_name="ambrosia_workspace")

    # Which .tex compiles, for a LaTeX workspace. A project holds many of them —
    # chapters, a class file, a poster — and only one is the document. SET_NULL
    # because deleting the main file must not delete the workspace; `latex
    # .resolve_main` then falls back to convention until someone picks again.
    main_file = models.ForeignKey(
        "vault.VaultFile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    description = models.TextField(blank=True)

    # Per-workspace overrides for how this workspace RUNS — the kernel's
    # timeouts and environment, the LaTeX engine and passes. Namespaced by the
    # language app that owns each key ({"dracena": {...}, "texlab": {...}}),
    # because ambrosia stores them and deliberately knows what none of them
    # mean: each lab declares its own fields through
    # `registry.WorkspaceApp.settings_fields` and the base validates against
    # that declaration (see settings_spec.py). One JSON column rather than
    # typed columns per lab keeps that ignorance honest — a new knob is a
    # declaration, not a migration.
    #
    # Empty means "the host defaults", so an untouched workspace behaves
    # exactly as it did before this field existed.
    settings = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_opened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_opened_at", "-created_at"]
        indexes = [
            models.Index(fields=["owner", "-last_opened_at"]),
            models.Index(fields=["kind"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "workspace"
            slug, n = base, 1
            while Workspace.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                n += 1
                slug = f"{base}-{n}"
            self.slug = slug
        super().save(*args, **kwargs)

    def touch(self):
        self.last_opened_at = timezone.now()
        self.save(update_fields=["last_opened_at", "updated_at"])

    @property
    def file_extension(self) -> str:
        return ".py" if self.kind == WorkspaceKind.PYTHON else ".tex"

    @property
    def default_file_type(self) -> str:
        return "python" if self.kind == WorkspaceKind.PYTHON else "latex"

    @property
    def uses_kernel(self) -> bool:
        return (self.kind == WorkspaceKind.PYTHON
                and self.execution == ExecutionMode.KERNEL)

    @property
    def is_latex(self) -> bool:
        return self.kind == WorkspaceKind.LATEX

    @property
    def run_verb(self) -> str:
        """What the button says. The room dispatches on this, not on the kind."""
        return "Compile" if self.is_latex else "Run"

    def settings_for(self, namespace: str) -> dict:
        """The raw stored settings one language app owns.

        Always a dict, never None: a workspace created before this field
        existed, or one nobody has configured, reads as "no overrides" rather
        than as an error at every call site.
        """
        stored = self.settings or {}
        section = stored.get(namespace) or {}
        return section if isinstance(section, dict) else {}

    def directory_ids(self) -> list[int]:
        """The root directory and every directory beneath it.

        This is the workspace's boundary. A bucket can hold several workspaces
        and any number of unrelated folders, so everything that asks "is this
        mine?" — the tree, opening a file, creating one, destroying the project —
        asks it against this list and never against the bucket.

        Walks in Python over one flat query rather than recursing per level:
        buckets are small, and a recursive CTE is not worth a raw query here.
        """
        from toto.vault.models import VaultDirectory

        if self.root_directory_id is None:
            return []
        children: dict[int, list[int]] = {}
        for pk, parent_id in VaultDirectory.objects.filter(
                bucket_id=self.bucket_id).values_list("pk", "parent_id"):
            children.setdefault(parent_id, []).append(pk)

        found, stack = [], [self.root_directory_id]
        while stack:
            current = stack.pop()
            found.append(current)
            stack.extend(children.get(current, ()))
        return found

    @property
    def path_label(self) -> str:
        """Where this workspace lives, for the UI: bucket / a / b / name."""
        if self.root_directory_id is None:
            return self.bucket.name
        return f"{self.bucket.name} / {self.root_directory.full_path()}"


class WorkspaceHibernation(models.Model):
    """What was written down when a workspace was put to sleep.

    One per workspace, reused: hibernating and waking the same workspace ten
    times keeps one row whose ``hibernated_at`` is set or cleared. ``manifest``
    is deliberately a JSONField rather than columns — the base stores what a lab
    hands it and never learns what any of it means, which is what lets a future
    runtime hibernate without a migration here.
    """

    workspace = models.OneToOneField(
        Workspace, on_delete=models.CASCADE, related_name="hibernation")
    #: Set while asleep, cleared on waking. The state, in one nullable field.
    hibernated_at = models.DateTimeField(null=True, blank=True, db_index=True)
    rehydrated_at = models.DateTimeField(null=True, blank=True)
    #: Base image, runtime versions, declared trees, vault snapshot, position —
    #: whatever the lab said to keep, plus what the base records itself.
    manifest = models.JSONField(default=dict, blank=True)
    #: The collected $HOME, on a permanent-home Gear. Absent for a plain
    #: manifest hibernation, which is the ordinary case.
    home = models.FileField(upload_to="ambrosia/hibernation/",
                            storage=_private_storage, blank=True, null=True)
    #: sha256 of those bytes, verified before anything is ever staged back: a
    #: home carries shell profiles and an interactive pip tree, so restoring one
    #: that is not what was stored would be executing something unaccounted for.
    home_digest = models.CharField(max_length=64, blank=True, default="")
    home_bytes = models.PositiveBigIntegerField(default=0)

    class Meta:
        verbose_name = "Workspace hibernation"
        verbose_name_plural = "Workspace hibernations"

    def __str__(self):
        state = "asleep" if self.hibernated_at else "awake"
        return f"{self.workspace.slug} ({state})"

    @property
    def depth(self) -> str:
        return (self.manifest or {}).get("depth", "manifest")
