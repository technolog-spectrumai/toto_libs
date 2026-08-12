"""Vault's levy provider: gigabytes held, sampled for the storage.gb_day levy.

Imported only by ``toto.tax``'s autodiscovery (``TaxConfig.ready()``), which
runs solely on hosts that install the economy — everywhere else this module is
never loaded. It imports nothing outside toto-base, so being discovered is the
only thing that makes it special.

Attribution is ``VaultFile.owner``, full stop: the uploader owns the bytes
wherever the bucket lives and whoever else can read them. Known quirks the
levy inherits rather than fixes: a copy keeps the source file's owner, and
``file_size_bytes`` can lag after an encrypt/decrypt rewrite.
"""

from __future__ import annotations


from toto.quota.levy import LevyProvider, registry


class StorageLevy(LevyProvider):
    code = "vault.storage"
    metric_code = "storage.gb_day"
    raw_per_unit = 2 ** 30  # binary GB, consistent with vault's MB = 2**20

    def sample(self):
        from django.db.models import Sum

        from .models import VaultFile

        rows = (VaultFile.objects
                .values("owner_id")
                .annotate(total=Sum("file_size_bytes")))
        for row in rows:
            yield row["owner_id"], int(row["total"] or 0)

    def measure(self, user) -> int:
        from django.db.models import Sum

        from .models import VaultFile

        agg = VaultFile.objects.filter(owner=user).aggregate(total=Sum("file_size_bytes"))
        return int(agg["total"] or 0)


# What falling behind means here. It used to mean permanent deletion of
# randomly chosen files, one week after a missed payment; it now means the
# vault stops accepting new ones. Nothing anybody uploaded is ever destroyed to
# settle a bill.
StorageLevy.consequence_text = (
    "no new uploads until it clears — nothing you have stored is deleted"
)

registry.register(StorageLevy())
