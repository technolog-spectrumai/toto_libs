"""Who may write a document the vault would refuse them.

The vault decides access from its own row — owner, staff, directory ACL,
public. A project-wiki page is none of those for the people who write it: the
file is held by the project lead and edited by the team, and only cyprian's
DocumentBridge knows that. So cyprian answers on the vault's behalf, and the
lock and version endpoints finally agree with the writer about who may edit.

Registered UNCONDITIONALLY, unlike the Edit button in
``vault_editor_plugins.py``, which is gated on ``document_editor_shown()``.
Hiding the writer's dashboard tile hides a way in; it must not withdraw a
team's editing lock, because the wiki keeps driving ``cyprian:edit`` either
way — its URLs stay mounted exactly for that reason.
"""

from toto.vault.plugins import VaultAccessPlugin

from ..bridge import may_edit


@VaultAccessPlugin.plugin(key="ctml", title="CTML Document")
class CtmlAccessPlugin(VaultAccessPlugin):
    file_type = "ctml"

    def may_edit(self, user, vault_file) -> bool:
        # No parsed document to hand over: the vault holds only the row. A
        # bridge that answers from its own column (the ones that matter here)
        # needs nothing more; one that would have to read meta re-reads, which
        # is the documented cost of asking without the document in hand.
        return may_edit(user, vault_file)


@VaultAccessPlugin.plugin(key="document", title="Document (legacy)")
class LegacyDocumentAccessPlugin(CtmlAccessPlugin):
    """The pre-CTML spelling, registered separately because `key` IS the lookup.

    `VaultAccessPlugin.for_file_type` is `registry.get(file_type)`, so one class
    cannot answer for two types. Migration 0023 retypes what it can reach, but
    it deliberately cannot reach mirrored stubs, remote buckets or encrypted
    rows — and for those, this registration is the difference between a kanban
    wiki collaborator keeping their lock and version rights and quietly losing
    them with no error anywhere.
    """

    file_type = "document"
