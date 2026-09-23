from django import template
from django.utils.safestring import mark_safe

from toto.core.plugin import FloatingPlugin, HeaderPlugin

register = template.Library()


@register.simple_tag(takes_context=True)
def render_floating_plugins(context):
    request = context.get("request")
    rendered = FloatingPlugin.render_all(request=request)
    return mark_safe("".join(r.html for r in rendered))


@register.simple_tag(takes_context=True)
def render_header_plugins(context, variant="bar"):
    """The app-bar widgets. ``variant`` is "bar" or "mobile" — the same
    plugin renders twice, and the template decides how much of itself to
    show at each width."""
    request = context.get("request")
    rendered = HeaderPlugin.render_all(request=request, variant=variant)
    return mark_safe("".join(r.html for r in rendered))
