from django.utils.safestring import mark_safe


class PageDetailMixin:
    """
    Renders sections from any Page-like model into a list of dicts compatible
    with verbena/base_page_detail.html or an app-specific reader template.

    NO CONSUMERS as of 1.49. ``toto.kanban.DocumentationPage`` was the only one,
    and its sections were folded into a single ``body_html`` when the page became
    a wiki page written in ``toto.cyprian``. This and
    ``verbena/base_page_detail.html`` are kept for now rather than deleted with
    the feature: toto-base ships to hosts outside this monorepo, and dropping a
    template out of a wheel payload is its own change with its own blast radius.
    Retire them deliberately, not as a side effect.
    """

    def render_sections(self, obj):
        sections = []
        for section in obj.sections.all():
            sections.append({
                "pk": section.pk,
                "title": section.title,
                "author": getattr(section, "author", None),
                "html": mark_safe(section.content),
            })
        return sections
