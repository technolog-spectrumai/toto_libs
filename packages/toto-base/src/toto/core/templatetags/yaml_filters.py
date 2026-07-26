# toto/templatetags/yaml_filters.py
import yaml
from django import template

register = template.Library()

@register.filter
def yaml_print(value):
    try:
        y = yaml.dump(value, default_flow_style=False, allow_unicode=True)
        return y
    except Exception as e:
        return value
