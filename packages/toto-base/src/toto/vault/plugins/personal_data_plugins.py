"""The vault's timeline in "Download my data": the rows of what the member
did to files and buckets (2026-10-12). What others did to the member's files
is theirs, not in this copy."""

from toto.core.personal_data import PersonalDataPlugin, Table


@PersonalDataPlugin.plugin(key="vault_timeline")
class VaultTimelineData(PersonalDataPlugin):
    def tables(self, user) -> list[Table]:
        from toto.vault import timeline
        from toto.vault.models import VaultEvent

        rows = [{"when": event.at.isoformat(), "bucket": event.bucket.name,
                 "file": event.file_title, "what": timeline.sentence(event), "kind": event.kind}
                for event in (VaultEvent.objects.filter(actor=user).select_related("bucket")
                              .order_by("at", "pk"))]
        return [Table("vault_timeline",
                      "What you did to files and buckets, as their history keeps it: "
                      "files created, edited, opened and downloaded, renames, moves, encryption, the trash, copies.",
                      rows)]
