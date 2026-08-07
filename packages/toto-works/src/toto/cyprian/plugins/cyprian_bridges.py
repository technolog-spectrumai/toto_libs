"""The contract bridge, expressed as a bridge.

notarius is a HOST app — zenobia owns it outright — so this wheel cannot import
it at module scope. Guarded the way the views always were: ``should_register``
answers False where notarius is absent, the key never enters the registry, and a
document carrying ``meta["contract"]`` is an ordinary document with an inert meta
field. That is exactly what a host without contracts has today.

This is a one-way import bridge for pre-rework ``.contract`` files. The 2026-08
rework made notarius signature-only, so nothing in its UI reaches
``cyprian:from_contract`` any more — see ``notarius/render.py``, which survives
only for this. The behaviour is preserved verbatim rather than improved: the
point of the port is that the mechanism now has two users instead of one, not
that this user changed.
"""

from django.apps import apps
from django.core.files.base import ContentFile

from toto.cyprian.bridge import DocumentBridge, read_raw
from toto.vault.models import VaultFile

CONTRACT_META = "contract"


@DocumentBridge.plugin(key=CONTRACT_META, title="Contract", order=20)
class ContractBridge(DocumentBridge):

    @classmethod
    def should_register(cls) -> bool:
        return apps.is_installed("toto.notarius")

    def claim(self, vault_file, document=None):
        """The contract this file is the body of.

        The link is a meta field here rather than a column, because notarius has
        no row to hang one on — a contract is itself just a vault file. That is
        weaker than the kanban bridge's column, and it is why this bridge grants
        nothing beyond what plain ownership already would: `can_edit` requires
        the contract to be the requester's own, so a forged `meta["contract"]`
        can only ever point at a file its author already controls. It also
        registers at a HIGHER order than the column-backed bridges, so it is
        asked last and can never shadow one of them.
        """
        raw_pk = (getattr(document, "meta", None) or {}).get(CONTRACT_META)
        if not raw_pk:
            return None
        try:
            return VaultFile.objects.get(pk=int(raw_pk))
        except (TypeError, ValueError, VaultFile.DoesNotExist):
            return None

    def can_edit(self, user, owner_object) -> bool:
        return (owner_object is not None
                and owner_object.owner_id == user.pk
                and not owner_object.is_encrypted)

    def write_back(self, document, owner_object, *, user) -> None:
        """Put the writer's HTML back into the contract it came from.

        So notarius's own Generate PDF renders what was just written — that is
        what made cyprian the editor rather than a copy of the text.
        """
        from toto.notarius import contract_format

        try:
            contract = contract_format.loads(read_raw(owner_object))
        except Exception:                              # noqa: BLE001
            return
        contract.content.media_type = "text/html"
        contract.content.encoding = "text"
        contract.content.data = document.content
        payload = contract_format.dumps(contract).encode("utf-8")
        owner_object.file.save(owner_object.title, ContentFile(payload), save=True)
        owner_object.content_hash = owner_object.create_hash()
        owner_object.file_size_bytes = owner_object.file.size
        owner_object.save(update_fields=["content_hash", "file_size_bytes"])

    def return_url(self, owner_object) -> str:
        from django.urls import NoReverseMatch, reverse

        try:
            return reverse("notarius:view", args=[owner_object.pk])
        except NoReverseMatch:
            return ""

    def return_label(self, owner_object) -> str:
        return owner_object.title
