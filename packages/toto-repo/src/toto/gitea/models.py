from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models



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


# NO USAGE-EVENT / QUOTA-POLICY PAIR. They existed for the `gitea.gb_day`
# levy — gigabytes of forge storage billed per day, the same shape as the
# vault's — and that levy was removed on 2026-09-05.
#
# WHY IT WENT. Access to the forge is already sold per SEAT: `gitea` and
# `repo` are entitlements on the Professional plan (subscriptions/plans.yaml).
# Billing the same feature a second time by the gigabyte was an overlapping
# charge, and it had never actually charged anybody — `ingress_tax` seeded the
# rule `active=False` and no seeder ever gave it a price, so it was dormant
# machinery rather than revenue.
#
# WHAT STAYED, and the distinction matters: the nightly task in `tasks.py`
# samples storage AND reconciles a cap. The cap is disk safety — over it, the
# forge stops accepting NEW repositories — and a 75 GB box still wants that.
# So `storage_bytes`, `storage_cap_gb`, `repo_creation_blocked` and
# `GiteaForgeSample` all remain; only the billing left.
