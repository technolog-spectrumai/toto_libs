from django import template
from django.utils.safestring import mark_safe

from toto.kanban.plugins.task_plugins import TaskPlugin

register = template.Library()


@register.simple_tag(takes_context=True)
def render_task_plugins(context, task):
    request = context.get("request")
    base_context = context.flatten()
    rendered = TaskPlugin.render_all(
        request=request,
        task=task,
        base_context=base_context,
    )
    return mark_safe("".join(section.html for section in rendered))
