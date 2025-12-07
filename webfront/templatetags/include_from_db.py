from django import template
from django.template import Template, Context
from webfront.models import HtmlTemplate

register = template.Library()


@register.tag
def include_from_db(parser, token):
    """
    Usage:
      {% include_from_db "TemplateName" with var1="value" var2=other_var %}
    """
    bits = token.split_contents()
    tag_name = bits[0]

    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            f"{tag_name} requires at least the template name"
        )

    template_name = bits[1].strip('"\'')
    extra_context = {}

    if len(bits) > 2:
        if bits[2] != "with":
            raise template.TemplateSyntaxError(
                f"{tag_name} optional arguments must start with 'with'"
            )
        for pair in bits[3:]:
            if "=" not in pair:
                raise template.TemplateSyntaxError(
                    f"{tag_name} arguments must be key=value"
                )
            key, val = pair.split("=", 1)
            extra_context[key] = val

    return IncludeFromDbNode(template_name, extra_context)


class IncludeFromDbNode(template.Node):
    def __init__(self, template_name, extra_context):
        self.template_name = template_name
        self.extra_context = extra_context

    def render(self, context):
        try:
            tmpl = HtmlTemplate.objects.get(name=self.template_name)
        except HtmlTemplate.DoesNotExist:
            return f"<!-- Template '{self.template_name}' not found -->"

        # Resolve values from context if they are variables
        resolved_context = {}
        for key, val in self.extra_context.items():
            if val in context:
                resolved_context[key] = context[val]
            else:
                resolved_context[key] = val.strip('"\'')

        # Merge with current context
        merged = Context({**context.flatten(), **resolved_context})

        # 🔁 Render recursively: if tmpl.content contains {% include_from_db %}, it will be re‑evaluated
        return Template(tmpl.content).render(merged)
