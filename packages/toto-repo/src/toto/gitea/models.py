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
