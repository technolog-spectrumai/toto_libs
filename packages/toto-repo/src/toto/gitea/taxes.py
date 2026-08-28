"""Gitea's levy provider: hosted-git gigabytes, for the gitea.gb_day levy.

Imported only by ``toto.tax``'s autodiscovery (``TaxConfig.ready()``) — on a
host without the economy this module is never loaded. It reads the SNAPSHOT
the nightly sampler writes (``GiteaAccount.storage_bytes``), never the forge:
the sweep and the "what would I pay" preview walk the database like every
other provider, and a forge that is down costs staleness, not a failed levy.

Attribution is the ``owner.login`` → ``GiteaAccount.username`` mapping the
sampler applied. Org-owned repositories map to nobody and are deliberately
NOT billed — never bill what you cannot attribute (the vault excludes its
mirror stubs for the same reason) — but their total is kept in
``GiteaForgeSample.unattributed_bytes`` so the unbilled remainder is a
visible number rather than a blind spot.
"""

from __future__ import annotations

from toto.quota.levy import LevyProvider, registry


class GiteaStorageLevy(LevyProvider):
    code = "gitea.storage"
    metric_code = "gitea.gb_day"
    raw_per_unit = 2 ** 30  # binary GB, consistent with the vault levy

    def sample(self):
        from .models import GiteaAccount

        rows = (GiteaAccount.objects
                .filter(storage_bytes__gt=0)
                .values_list("user_id", "storage_bytes"))
        for user_id, raw in rows:
            yield user_id, int(raw)

    def measure(self, user) -> int:
        from .models import GiteaAccount

        account = GiteaAccount.objects.filter(user=user).first()
        return int(account.storage_bytes) if account else 0


# What falling behind means here: the same never-destroy rule as the vault.
GiteaStorageLevy.consequence_text = (
    "no new repositories until it clears — nothing you have pushed is deleted"
)

registry.register(GiteaStorageLevy())
