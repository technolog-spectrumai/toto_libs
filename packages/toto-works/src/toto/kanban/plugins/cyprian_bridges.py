"""A wiki page's prose belongs to its project, not to whoever holds the bytes.

This is the bridge that makes cyprian usable as a *shared* editor. Cyprian's own
rule is ownership: the writer opens for `VaultFile.owner` and 404s for everyone
else. That is right for a personal document and useless for a project wiki,
where the file is held by the project lead and written by the team.

Discovery is cyprian's, from `CyprianConfig.ready()`, so this module is imported
only on a host that has the writer. studio and aurelian install kanban from the
same wheel, never import this, and render their pages read-only from
`body_html` — which is why that column exists.

**The security property.** `claim()` answers from `DocumentationPage.vault_file`,
a column written by `cyprian.bridge.open_document` and by nothing else — it is
`editable=False` on the model, absent from `WikiPageForm`, and read-only in
admin. That is deliberate and it is the whole defence: if a project member could
set that pk, they could point a page at any document on the instance and this
bridge would faithfully authorise them onto it. The document's `meta` is not
consulted here for the same reason — anyone who can save a document can write
its meta.
"""

from toto.cyprian.bridge import DocumentBridge
from toto.kanban.models import KANBAN_PAGE_META


@DocumentBridge.plugin(key=KANBAN_PAGE_META, title="Wiki page", order=10)
class KanbanPageBridge(DocumentBridge):
    """Order 10: asked before the meta-only contract bridge, which is order 20.

    A column-backed claim must always outrank one that can only read the
    document, or a forged `meta["contract"]` on a wiki page's file could decide
    who edits it.
    """

    #: A wiki page is part of Tasks, so Tasks is the plan that covers writing
    #: one. Without this, moving the document writer to a higher tier would
    #: 402 every project wiki on the lower one.
    entitlement = "kanban"

    @classmethod
    def should_register(cls) -> bool:
        from django.apps import apps

        return apps.is_installed("toto.kanban")

    def claim(self, vault_file, document=None):
        from toto.kanban.models import DocumentationPage

        return (
            DocumentationPage.objects
            .select_related("project", "project__project_lead")
            .filter(vault_file=vault_file)
            .first()
        )

    def can_edit(self, user, owner_object) -> bool:
        # The board's own answer to "who works on this project": staff, a project
        # auditor, or an active Practitioner with a live commitment. Imported
        # from views rather than restated, so the wiki and the board can never
        # disagree about who is on the team.
        from toto.kanban.views import can_manage_tasks

        return can_manage_tasks(user, owner_object.project)

    def can_read(self, user, owner_object) -> bool:
        from toto.kanban.views import can_read_project

        return can_read_project(user, owner_object.project)

    def write_back(self, document, owner_object, *, user) -> None:
        """The saved body, back onto the row every host renders.

        `body_html` is the read model — studio, aurelian and any cyprian-less
        build show this and nothing else — so a save that did not land here
        would be a save nobody outside the writer could see. It is already
        sanitised: `Document.from_dict` runs `sanitize_content` on the way in,
        which is what makes rendering it with `|safe` a fact rather than a hope.

        The title follows the document, because the writer's header is the only
        title the author sees while they are writing; letting the two drift
        would mean renaming a page had to happen somewhere else entirely.
        """
        fields = ["body_html"]
        owner_object.body_html = document.content or ""
        title = (document.title or "").strip()
        if title and title != owner_object.title:
            owner_object.title = title
            fields.append("title")
        owner_object.save(update_fields=fields)

    def return_url(self, owner_object) -> str:
        return owner_object.get_absolute_url()

    def return_label(self, owner_object) -> str:
        return owner_object.title
