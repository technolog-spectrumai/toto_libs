"""A link from a metered app to its own slice of the usage page.

Deliberately a template tag rather than a plugin registry. `toto.quota` is
mounted unconditionally on every host, so ``quota:my_usage_app`` always
reverses — there is no NoReverseMatch to dodge and therefore nothing a registry
would buy, while `BasePlugin.register` raising on a duplicate key is a real cost
to take on for a static link.

Usage, one line in the app's own nav::

    {% load quota_tags %}
    {% quota_tab "texlab" %}
"""

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import registry

register = template.Library()


@register.inclusion_tag("quota/partials/_quota_tab.html")
def quota_tab(app_label, label=None):
    """A usage link for this app, or nothing when it meters nothing.

    Checking the registry is what keeps the tab honest: an app that declares no
    metric would otherwise link to a page listing nothing, and a plugin left
    behind after its metric was deleted would become a dead tab. Here the answer
    follows the registry automatically.
    """
    if not registry.for_app(app_label):
        return {"url": ""}
    try:
        url = reverse("quota:my_usage_app", args=[app_label])
    except NoReverseMatch:  # pragma: no cover - quota is mounted everywhere
        url = ""
    return {"url": url, "label": label or _("Usage"),
            "icon": "fa-solid fa-gauge-high"}
