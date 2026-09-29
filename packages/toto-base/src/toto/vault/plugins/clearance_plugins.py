"""Vault files a new clearance may keep (2026-09-30; socialhub's
``ClearanceTargetPlugin``). Sheets and decks are vault files too, offered as
their own kinds by the apps that show them (primula, memo), which subclass
``VaultFileKind`` with their ``vault_file_types``; the generic "Files" kind
leaves those types to them. Mirrored files are never offered: their
metadata belongs to the host they came from."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from django.db.models import Q

from toto.socialhub.plugins.clearance_plugins import SEARCH_LIMIT, ClearanceTargetPlugin, add_to, kinds


class VaultFileKind(ClearanceTargetPlugin):
    #: The file types this kind offers; empty = every type no other kind claims.
    vault_file_types: tuple = ()

    def _files(self):
        from toto.vault.models import FileOrigin, VaultFile

        files = VaultFile.objects.exclude(origin=FileOrigin.MIRROR)
        if self.vault_file_types:
            return files.filter(file_type__in=self.vault_file_types)
        claimed = {t for other in kinds() if other is not self
                   for t in getattr(other, "vault_file_types", ())}
        return files.exclude(file_type__in=claimed)

    def search(self, q, limit=SEARCH_LIMIT):
        found = (self._files().filter(Q(title__icontains=q) | Q(key__icontains=q))
                 .select_related("bucket").order_by("title", "pk")[:limit])
        return [self.row(f) for f in found]

    def resolve(self, pks):
        return list(self._files().filter(pk__in=pks).select_related("bucket").order_by("title", "pk"))

    def keep(self, objects, clearance, *, actor):
        from toto.vault.clearances import clearances_of, set_clearances

        for vault_file in objects:
            set_clearances(vault_file, add_to(clearances_of(vault_file), clearance), actor=actor)
        return len(objects)

    def label(self, obj):
        return obj.title or obj.key or f"#{obj.pk}"

    def detail(self, obj):
        bucket = getattr(obj, "bucket", None)
        return " · ".join(part for part in (obj.file_type, bucket.name if bucket else "") if part)


@ClearanceTargetPlugin.plugin(key="vault.file", title=_("Files"), order=20)
class VaultFiles(VaultFileKind):
    icon = "file"
