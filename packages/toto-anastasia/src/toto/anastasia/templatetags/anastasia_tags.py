"""One filter, because a Gear's warm policy is a dict keyed by family.

Django's template language cannot subscript a dict by a variable key, and the
alternative — flattening the policy into the view for every family — puts
presentation shape into the service layer. One four-line filter is the smaller
compromise.
"""

from django import template

register = template.Library()


@register.filter
def dictget(mapping, key):
    """``{{ some_dict|dictget:key }}``. Missing keys are empty, never an error."""
    if not hasattr(mapping, "get"):
        return ""
    return mapping.get(key, "")
