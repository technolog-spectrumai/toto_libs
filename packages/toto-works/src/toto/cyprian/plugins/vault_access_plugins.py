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


@VaultAccessPlugin.plugin(key="document", title="Document")
class DocumentAccessPlugin(VaultAccessPlugin):
    file_type = "document"

    def may_edit(self, user, vault_file) -> bool:
        # No parsed document to hand over: the vault holds only the row. A
        # bridge that answers from its own column (the ones that matter here)
        # needs nothing more; one that would have to read meta re-reads, which
        # is the documented cost of asking without the document in hand.
        return may_edit(user, vault_file)
