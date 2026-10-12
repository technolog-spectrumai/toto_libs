from django import template
from django.utils.safestring import mark_safe

from toto.core.plugin import FloatingPlugin, HeaderPlugin

register = template.Library()


@register.simple_tag(takes_context=True)
def render_floating_plugins(context):
    request = context.get("request")
    rendered = FloatingPlugin.render_all(request=request)
    return mark_safe("".join(r.html for r in rendered))


#: ``group`` of ``render_header_plugins`` to ``HeaderPlugin.render_all(pinned=…)``.
GROUPS = {"": None, "pinned": True, "folding": False}


@register.simple_tag(takes_context=True)
def render_header_plugins(context, variant="bar", group=""):
    """The app-bar widgets. ``variant`` is "bar" or "mobile": the same
    plugin renders twice, once on the bar and once in the menu the bar folds
    into. ``group`` narrows it (2026-10-12): "pinned" is the widgets that
    stay on the bar at every width (``HeaderPlugin.pinned``), "folding" the
    rest, which the header hides below its breakpoint; "" is all of them.
    Any other word is nothing, so a misspelt group shows as a missing widget
    and not as every widget twice."""
    request = context.get("request")
    if group not in GROUPS:
        return ""
    rendered = HeaderPlugin.render_all(request=request, variant=variant, pinned=GROUPS[group])
    return mark_safe("".join(r.html for r in rendered))
