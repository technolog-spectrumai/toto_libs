"""Who may write a wiki page's file that the vault would refuse them.

The vault decides access from its own row — owner, staff, directory ACL,
public. A project-wiki page is none of those for the people who write it: the
file is held by the project lead and edited by the team, and only cyprian's
DocumentBridge knows that. So cyprian answers on the vault's behalf, and the
lock and version endpoints agree with the writer about who may edit.

KEYED ON `html` SINCE 2026-08-29, where it was `ctml` and `document` before.
The writer stores an ordinary HTML page now, so that is the type the wiki's
files carry and the type this rule has to answer for.

Claiming a type as broad as `html` is safe, and the reason is in
`vault.access.may_edit_via_app`: it is asked only AFTER the vault's own checks
have said no, and *it only ever widens*. For an ordinary HTML page no bridge
claims the file, `bridge.may_edit` returns False, and the vault's answer stands
unchanged. The only files this can widen access to are the ones an owning app
has put its own column against.

Registered UNCONDITIONALLY. Hiding a way in must never withdraw a team's
editing lock — access is not visibility, and the wiki keeps driving
`cyprian:edit` regardless of what any dashboard shows.
"""

from toto.vault.plugins import VaultAccessPlugin

from ..bridge import may_edit


@VaultAccessPlugin.plugin(key="html", title="HTML document")
class HtmlDocumentAccessPlugin(VaultAccessPlugin):
    file_type = "html"

    def may_edit(self, user, vault_file) -> bool:
        # No parsed document to hand over: the vault holds only the row. A
        # bridge that answers from its own column — the one that matters here,
        # kanban's — needs nothing more.
        return may_edit(user, vault_file)
