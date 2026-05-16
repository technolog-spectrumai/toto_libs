from django.utils.safestring import mark_safe


class PageDetailMixin:
    """
    Renders sections from any Page-like model into a list of dicts compatible
    with verbena/base_page_detail.html or an app-specific reader template.
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
