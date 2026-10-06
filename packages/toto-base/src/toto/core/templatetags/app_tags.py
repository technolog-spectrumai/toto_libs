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


@register.simple_tag
def assistant_installed():
    """Whether this host has the assistant (``toto.core.assistant.installed``).

    ``{% assistant_installed as has_assistant %}`` — for what a page draws
    only for the assistant: a bucket's AI shield, a sentence that names it.
    """
    from toto.core import assistant

    return assistant.installed()


@register.filter
def economy_hidden(user):
    """True when this host shows ``user`` mana instead of the economy.

    ``toto.quota.rates.economy_hidden_from``, for templates — so the economy
    strip, the gas pump and the usage tab all ask one question and cannot
    disagree about who sees what.
    """
    from toto.quota.rates import economy_hidden_from

    return economy_hidden_from(user)
