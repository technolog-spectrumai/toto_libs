from django import template
from django.utils.safestring import mark_safe

from toto.core.plugin import FloatingPlugin

register = template.Library()


@register.simple_tag(takes_context=True)
def render_floating_plugins(context):
    request = context.get("request")
    rendered = FloatingPlugin.render_all(request=request)
    return mark_safe("".join(r.html for r in rendered))
