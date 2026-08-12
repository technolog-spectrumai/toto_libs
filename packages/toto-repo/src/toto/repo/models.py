from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


class GitRepo(models.Model):
    """A vault directory turned into a local git repository.

    The repository's working tree is a MATERIALIZED MIRROR of the vault
    directory subtree (vault DB stays the source of truth between git
    operations) — see git_integration.md. One repo per directory; nesting a
    repo under another repo's subtree is rejected at init time.
    """

    directory = models.OneToOneField(
        "vault.VaultDirectory", on_delete=models.CASCADE, related_name="git_repo"
    )
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    default_branch = models.CharField(max_length=100, default="main")
    # The origin, verbatim — ANY git URL, whether the user typed it or another
    # app filled it in. One field and no second spelling: the pre-split model
    # also carried an owner/name pair for the co-deployed Gitea and derived a
    # URL from them, which meant two mutually exclusive ways to be connected and
    # a rule about clearing whichever one you were not using. A URL is the thing
    # git actually takes.
    #
    # Pushed to as-is with no token baked in. Credentials, when there are any,
    # come from a provider that CLAIMS this URL at push time (see remotes.py);
    # with no provider installed a private remote simply surfaces git's own auth
    # error, which is honest — toto.repo stores no third-party credentials.
    remote_url = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"GitRepo({self.directory})"

    @property
    def base_dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / "repo" / str(self.pk)

    @property
    def worktree(self) -> Path:
        return self.base_dir / "worktree"

    @property
    def remote_connected(self) -> bool:
        return bool(self.remote_url)


class GitRepoFile(models.Model):
    """vault file ⇄ worktree path correspondence.

    Owned exclusively by the sync engine (mutated only under the repo lock);
    lets import/export distinguish renames, deletions and brand-new files
    deterministically.
    """

    repo = models.ForeignKey(GitRepo, on_delete=models.CASCADE, related_name="files")
    vault_file = models.OneToOneField(
        "vault.VaultFile", on_delete=models.CASCADE, related_name="git_mapping"
    )
    relpath = models.CharField(max_length=1024)

    class Meta:
        unique_together = ("repo", "relpath")

    def __str__(self):
        return f"{self.repo_id}:{self.relpath}"


class GitRun(models.Model):
    """One background git network operation (push/pull) — fileservices'
    FileServiceRun shape, so the UI can reuse the familiar poll-until-done
    pattern."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
        (SUCCESS, "Success"),
        (FAILED, "Failed"),
    ]
    OP_CHOICES = [("init", "Init"), ("push", "Push"), ("pull", "Pull")]

    repo = models.ForeignKey(GitRepo, on_delete=models.CASCADE, related_name="runs")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    op = models.CharField(max_length=10, choices=OP_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    # Link into the workflows app when dispatched through it (fileservices
    # pattern) — the run then shows up in the workflows UI. GitRun remains the
    # repo UI's own status tracker either way.
    workflow_run = models.ForeignKey(
        "workflows.WorkflowRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="git_runs",
    )
    stdout = models.TextField(blank=True, default="")
    stderr = models.TextField(blank=True, default="")
    # {created: [...], updated: [...], deleted: [...]} after a pull's import.
    import_summary = models.JSONField(null=True, blank=True)
    task_id = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"GitRun({self.op} {self.repo_id} {self.status})"


# --------------------------------------------------------------------------- #
# Metering                                                                     #
# --------------------------------------------------------------------------- #
# toto.quota owns no tables, so each metered app declares its own concrete pair
# and the rows live in that app's migrations. See toto/quota/models.py.
# Metrics: repo.run (init/push/pull) and repo.op (in-request git).

class RepoUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Repo usage event"
        verbose_name_plural = "Repo usage events"


class RepoQuotaPolicy(AbstractQuotaPolicy):
    events = RepoUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Repo quota policy"
        verbose_name_plural = "Repo quota policies"
