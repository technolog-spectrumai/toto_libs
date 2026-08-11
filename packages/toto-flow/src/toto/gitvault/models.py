from pathlib import Path

from cryptography.fernet import Fernet
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
    # Set when connected to the co-deployed Gitea ("" until then). The Gitea
    # remote URL is always derived from these + GITEA_INTERNAL_URL, never
    # persisted.
    remote_owner = models.CharField(max_length=100, blank=True, default="")
    remote_name = models.CharField(max_length=100, blank=True, default="")
    # OR a custom remote, verbatim — any git URL the user typed at init or in
    # the repo panel. Mutually exclusive with the Gitea pair (connect clears
    # whichever side it replaces). Pushed to as-is with no token injection: a
    # private HTTP remote surfaces git's own auth error, which is honest —
    # gitvault stores no third-party credentials.
    remote_url = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"GitRepo({self.directory})"

    @property
    def base_dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / "gitvault" / str(self.pk)

    @property
    def worktree(self) -> Path:
        return self.base_dir / "worktree"

    @property
    def remote_connected(self) -> bool:
        return bool(self.remote_url or (self.remote_owner and self.remote_name))

    @property
    def uses_gitea(self) -> bool:
        return bool(self.remote_owner and self.remote_name) and not self.remote_url


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


class GiteaAccount(models.Model):
    """Auto-provisioned Gitea identity for a portal user.

    The access token is minted through Gitea's admin API (see gitea_client)
    and stored Fernet-encrypted with FIELD_ENCRYPTION_KEY — the raw value
    never lands in the DB or in .git/config (push/pull inject it per
    invocation via an http.extraHeader).
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="gitea_account")
    username = models.CharField(max_length=100)
    token_encrypted = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"GiteaAccount({self.user.username} → {self.username})"

    @staticmethod
    def _fernet() -> Fernet:
        return Fernet(settings.FIELD_ENCRYPTION_KEY.encode())

    def set_token(self, raw: str) -> None:
        self.token_encrypted = self._fernet().encrypt(raw.encode()).decode()

    def get_token(self) -> str:
        if not self.token_encrypted:
            return ""
        return self._fernet().decrypt(self.token_encrypted.encode()).decode()


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
    # gitvault UI's own status tracker either way.
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
# Metrics: gitvault.run (init/push/pull) and gitvault.op (in-request git).

class GitvaultUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Gitvault usage event"
        verbose_name_plural = "Gitvault usage events"


class GitvaultQuotaPolicy(AbstractQuotaPolicy):
    events = GitvaultUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Gitvault quota policy"
        verbose_name_plural = "Gitvault quota policies"
