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

        rows = (self._billable(VaultFile.objects)
                .values("owner_id")
                .annotate(total=Sum("file_size_bytes")))
        for row in rows:
            yield row["owner_id"], int(row["total"] or 0)

    def measure(self, user) -> int:
        from django.db.models import Sum

        from .models import VaultFile

        agg = (self._billable(VaultFile.objects.filter(owner=user))
               .aggregate(total=Sum("file_size_bytes")))
        return int(agg["total"] or 0)

    @staticmethod
    def _billable(qs):
        # Rows in a mounted remote bucket are mirror stubs: the bytes are held
        # (and billed) on the exporting host, so charging them here would bill
        # the same gigabyte twice. S3 rows keep billing — this host pays the
        # provider for them. One filter used by sample() AND measure(), so the
        # sweep and the "what would I pay" preview cannot disagree.
        from .models import StorageBackend

        return qs.exclude(bucket__storage_backend=StorageBackend.REMOTE_TOTO)


# What falling behind means here. It used to mean permanent deletion of
# randomly chosen files, one week after a missed payment; it now means the
# vault stops accepting new ones. Nothing anybody uploaded is ever destroyed to
# settle a bill.
StorageLevy.consequence_text = (
    "no new uploads until it clears — nothing you have stored is deleted"
)

registry.register(StorageLevy())


class PlaintextLevy(StorageLevy):
    """Gigabytes held UNENCRYPTED — what drains security mana.

    The storage levy narrowed to ``is_encrypted=False``, and nothing else: the
    same owner attribution, the same mirror-stub exclusion, one ``_billable``
    shared by the sweep and the live preview so they cannot disagree. Encrypting
    a file takes it out of the count the next night.
    """

    code = "vault.plaintext"
    metric_code = "security.plain_gb_day"

    @staticmethod
    def _billable(qs):
        return StorageLevy._billable(qs).filter(is_encrypted=False)


PlaintextLevy.consequence_text = ""

registry.register(PlaintextLevy())
