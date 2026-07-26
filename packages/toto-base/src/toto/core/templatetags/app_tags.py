from django import template
from django.apps import apps

register = template.Library()


@register.filter
def app_installed(app_label):
    """True if the given app (e.g. "toto.weather") is in INSTALLED_APPS.

    Lets templates guard links to optional/studio-only apps so their
    {% url %} tags are never reversed when the app is absent.
    """
    return apps.is_installed(app_label)
