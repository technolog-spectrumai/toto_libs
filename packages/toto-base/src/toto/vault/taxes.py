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

import random

from toto.quota.levy import EnforcementResult, LevyProvider, registry


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

    def enforce(self, user, target_raw: int, *, rng=None,
                on_deleted=None, on_skipped=None) -> EnforcementResult:
        """Delete randomly chosen files until the user holds ≤ ``target_raw``.

        Random in Python over ``(pk, size)`` pairs rather than ``order_by("?")``
        — a seeded ``rng`` makes the selection testable, and the table is not
        sorted server-side on every attempt. Each file's row delete and its
        audit callback commit atomically together (the blob follows on commit
        via :func:`~toto.vault.purge.purge_file`), so a crash mid-walk loses
        nothing: the next run re-measures and continues toward the target.
        Files pinned by a PROTECT FK are skipped and another is drawn.
        """
        from django.db import transaction
        from django.db.models import ProtectedError

        from .models import VaultFile
        from .purge import purge_file

        result = EnforcementResult()
        rows = list(VaultFile.objects.filter(owner=user)
                    .values_list("pk", "file_size_bytes"))
        (rng or random.SystemRandom()).shuffle(rows)

        remaining = sum(size for _pk, size in rows)
        for pk, size in rows:
            if remaining <= target_raw:
                break
            vault_file = VaultFile.objects.filter(pk=pk).first()
            if vault_file is None:  # deleted underneath us; its bytes are gone
                remaining -= size
                continue
            info = {
                "pk": pk,
                "title": vault_file.title,
                "key": vault_file.key,
                "bucket": getattr(vault_file.bucket, "slug", "") or "",
                "size": size,
            }
            try:
                with transaction.atomic():
                    purge_file(vault_file)
                    if on_deleted is not None:
                        on_deleted(info)
            except ProtectedError:
                result.skipped_count += 1
                if on_skipped is not None:
                    on_skipped(info)
                continue
            result.deleted_count += 1
            result.deleted_raw += size
            remaining -= size

        result.final_raw = self.measure(user)
        result.reached_target = result.final_raw <= target_raw
        return result


registry.register(StorageLevy())
