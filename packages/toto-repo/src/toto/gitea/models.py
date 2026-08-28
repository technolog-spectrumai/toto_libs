from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


class GiteaAccount(models.Model):
    """Auto-provisioned Gitea identity for a portal user.

    ``auth.User`` is this model's ONLY foreign key, and that is not an accident:
    it is what lets ``toto.gitea`` be installed on a host that has no local
    repositories at all. Zenobia is exactly that host — it hosts code in Gitea
    and versions its documents through ``toto.vault``, never through a worktree.

    The access token is minted through Gitea's admin API (see ``client``) and
    stored Fernet-encrypted with ``FIELD_ENCRYPTION_KEY`` — the raw value never
    lands in the database or in ``.git/config``. Where a host DOES also install
    ``toto.repo``, push and pull inject it per invocation through an
    ``http.extraHeader`` (see ``remotes``).
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="gitea_account")
    username = models.CharField(max_length=100)
    token_encrypted = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    #: The nightly sampler's snapshot of this user's repositories on the
    #: forge, in bytes. A COLUMN rather than a live API call so the levy
    #: sweep and the cap check read the database like every other provider,
    #: and a forge that is down costs staleness, never a failed sweep.
    storage_bytes = models.BigIntegerField(default=0)
    storage_sampled_at = models.DateTimeField(null=True, blank=True)
    #: Per-user override of settings.GITEA_STORAGE_CAP_GB. Null = the host
    #: default; both null = uncapped.
    storage_cap_gb = models.PositiveIntegerField(null=True, blank=True)
    #: What the reconciler last set forge-side (max_repo_creation=0), so the
    #: flip is idempotent and an operator can SEE who is blocked without
    #: asking the forge.
    repo_creation_blocked = models.BooleanField(default=False)

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


class GiteaForgeSample(models.Model):
    """One row: the whole forge, as of the last sample.

    ``unattributed_bytes`` is the org-owned and unmapped remainder — bytes the
    levy deliberately skips (never bill what you cannot attribute, the
    vault's mirror-stub rule) but must not hide: unbilled storage should be a
    number somebody can see, not a blind spot.
    """

    sampled_at = models.DateTimeField()
    total_bytes = models.BigIntegerField(default=0)
    unattributed_bytes = models.BigIntegerField(default=0)

    def __str__(self):
        return f"forge sample {self.sampled_at:%Y-%m-%d %H:%M}"


# toto.quota owns no tables, so each metered app declares its own concrete
# pair and the rows live in that app's migrations — the same shape as
# toto.repo. Without them toto.tax refuses the levy outright ("has no
# usage-event table"). Metric: gitea.gb_day.

class GiteaUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Gitea usage event"
        verbose_name_plural = "Gitea usage events"


class GiteaQuotaPolicy(AbstractQuotaPolicy):
    events = GiteaUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Gitea quota policy"
        verbose_name_plural = "Gitea quota policies"
