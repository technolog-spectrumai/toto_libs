"""The Management page's share / connect modals, template side.

``{% share_connect_config as sc %}`` hands the modals what they need — who
is looking, this Zenobia's address, the federated hosts to suggest, the
connect doors' links, the sentences the browser shows — as one dict, for
``json_script`` and for the template's own conditions
(``share_views.page_config``; nothing secret in it). A tag rather than the
list view's context, so the flow extends the page through its partials
alone.
"""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def share_connect_config(context) -> dict:
    from toto.vault.share_views import page_config

    return page_config(context.get("request"))
