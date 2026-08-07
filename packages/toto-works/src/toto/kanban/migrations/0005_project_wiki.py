"""Documentation pages become a per-project wiki.

A page was one-to-one with a Mission, its prose lived in ordered
DocumentationSection rows of Trix HTML, and there was no way to write one
outside Django admin. This makes the PROJECT the space, gives pages a parent so
they form a tree, folds every section into a single ``body_html`` on the page,
and adds the pointer to the cyprian document the prose is now written in.

Order is not negotiable. ``project`` arrives nullable, is backfilled from
``mission.campaign.project``, and only then becomes NOT NULL — and ``mission``
only becomes nullable AFTER that, because until the backfill has run the mission
chain is the sole source of the answer.

The backfill is total by construction: ``Campaign.project`` and
``Mission.campaign`` are both NOT NULL, and ``DocumentationPage.mission`` was NOT
NULL until this migration, so every existing page resolves to exactly one
project. A row that somehow does not is left NULL on purpose, and the AlterField
that follows fails loudly on it rather than silently reparenting someone's prose.

Irreversible on purpose — see raise_irreversible. Snapshot the database before
running this; that snapshot is the only rollback.

Studio and aurelian install kanban from the same wheel and will run this against
live data. It touches only the two documentation models: no Task, Mission,
Campaign or Project structure changes, so aurelian's five inbound FKs and the
four migration graphs that depend on ('kanban','0002_initial') are unaffected.
"""

from django.db import migrations, models
from django.utils.html import escape
import django.db.models.deletion


def backfill_project(apps, schema_editor):
    """Every page's project, from the mission it used to hang off."""
    Page = apps.get_model("kanban", "DocumentationPage")
    # Historical model, never the real one: this must keep working the day 0006
    # lands and changes the class.
    for page in Page.objects.select_related("mission__campaign").iterator():
        if page.mission_id and page.project_id is None:
            page.project_id = page.mission.campaign.project_id
            page.save(update_fields=["project"])


def fold_sections_into_body(apps, schema_editor):
    """One HTML body per page, in section order, titles promoted to headings.

    A cyprian document is a single body with no section boundaries, so keeping
    the rows as well would mean two representations of the same prose and a
    splitting rule to invent on every save. The heading is how a section titles
    itself in the writer, so that is what a section title becomes.
    """
    Page = apps.get_model("kanban", "DocumentationPage")
    Section = apps.get_model("kanban", "DocumentationSection")

    by_page = {}
    for section in Section.objects.order_by("page_id", "order", "pk").iterator():
        by_page.setdefault(section.page_id, []).append(section)

    for page in Page.objects.iterator():
        chunks = []
        for section in by_page.get(page.pk, ()):
            title = (section.title or "").strip()
            if title:
                # Escaped, because a section title is a CharField and has never
                # been HTML — a stray "<" in one would otherwise become markup
                # the moment it reached the body.
                chunks.append(f"<h2>{escape(title)}</h2>")
            body = (section.content or "").strip()
            if body:
                chunks.append(body)
        if chunks:
            page.body_html = "\n".join(chunks)
            page.save(update_fields=["body_html"])


def raise_irreversible(apps, schema_editor):
    raise RuntimeError(
        "kanban.0005 cannot be reversed. Reversing would re-add the GLOBAL "
        "unique on slug, which fails the moment two projects have a page with "
        "the same name — the ordinary outcome of using a wiki — and the fold "
        "into body_html is not injective: section boundaries, per-section "
        "authors and their uids are gone. The page tree and every vault_file "
        "pointer would go with it. Restore the database snapshot taken before "
        "this migration."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0004_visibility_zones_events_attachments"),
        ("vault", "0012_alter_vaultfile_file_type"),
    ]

    operations = [
        # --- new columns, project nullable for now ---------------------------
        migrations.AddField(
            model_name="documentationpage",
            name="project",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="wiki_pages",
                to="kanban.project",
            ),
        ),
        migrations.AddField(
            model_name="documentationpage",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="kanban.documentationpage",
            ),
        ),
        migrations.AddField(
            model_name="documentationpage",
            name="order",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="documentationpage",
            name="body_html",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="documentationpage",
            name="vault_file",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="kanban_wiki_pages",
                to="vault.vaultfile",
            ),
        ),

        # --- move the data before the shape changes under it ----------------
        migrations.RunPython(backfill_project, raise_irreversible, elidable=False),
        migrations.RunPython(fold_sections_into_body, migrations.RunPython.noop,
                             elidable=False),

        # --- now tighten -----------------------------------------------------
        migrations.AlterField(
            model_name="documentationpage",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="wiki_pages",
                to="kanban.project",
            ),
        ),
        migrations.AlterField(
            model_name="documentationpage",
            name="mission",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional: the mission this page is about.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="wiki_pages",
                to="kanban.mission",
            ),
        ),
        migrations.AlterField(
            model_name="documentationpage",
            name="is_manual",
            field=models.BooleanField(
                default=False,
                help_text="If true, this page is an instruction / how-to manual.",
            ),
        ),
        # Drops the unique index inherited from verbena.AbstractPage. The
        # composite that replaces it is strictly weaker, so no existing row can
        # violate it.
        migrations.AlterField(
            model_name="documentationpage",
            name="slug",
            field=models.SlugField(blank=True),
        ),
        migrations.AlterModelOptions(
            name="documentationpage",
            options={
                "ordering": ["order", "title"],
                "verbose_name": "Documentation Page",
                "verbose_name_plural": "Documentation Pages",
            },
        ),
        migrations.AddConstraint(
            model_name="documentationpage",
            constraint=models.UniqueConstraint(
                fields=("project", "slug"),
                name="kanban_docpage_slug_per_project",
            ),
        ),

        # --- the sections are in the body now --------------------------------
        migrations.DeleteModel(name="DocumentationSection"),
    ]
