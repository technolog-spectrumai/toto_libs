"""The public roster of offices.

An institution nobody can see is not an institution — the whole point of a
station is that people know the office exists, what it is for, and who to ask.
So the roster is public and needs no login.

What it renders: name, charter, holder, vacancy, and **who pays it** — which is
always the federal treasury, and is spelled out on every row rather than
implied, because it is the fact most easily got wrong. What it never renders:
the capability booleans, the limit multiplier and the stipend amount. Those are
admin-only, exactly as :class:`~toto.socialhub.models.CommunityPrivilege` is.
"""

from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from toto.ui import PageProcessor

from ..models import Station


def _render(request, template_name, context):
    """Every page on this platform goes through the decorator.

    ``PageProcessor.decorate`` is what supplies ``platform``, ``theme``,
    ``font``, ``logo`` and the header navigation. A template extending
    ``oya/base.html`` rendered without it loses the entire palette — the theme
    is a database record, not a stylesheet, so the page comes out unstyled
    rather than merely unbranded. This module was the only one in socialhub
    calling ``render`` bare, which is exactly how it looked.
    """
    return render(request, template_name,
                  PageProcessor().decorate(context, request))


def station_list(request):
    stations = (Station.objects
                .filter(active=True)
                .select_related("holder", "serves")
                .order_by("serves__name", "name"))

    # Two groups, because "serves a community" is the distinction a reader
    # actually wants — and grouping them makes it obvious that the federal
    # payer is the same on both sides.
    federal = [s for s in stations if s.serves_id is None]
    local = [s for s in stations if s.serves_id is not None]

    return _render(request, "socialhub/station_list.html", {
        "station_groups": [
            {
                "title": _("Offices of the platform"),
                "blurb": _("Work that serves everyone."),
                "rows": federal,
            },
            {
                "title": _("Offices serving a community"),
                "blurb": _(
                    "Local work, funded federally — a community asks for an "
                    "office and the platform creates and pays it."
                ),
                "rows": local,
            },
        ],
        "federal_stations": federal,
        "local_stations": local,
        "vacancies": sum(1 for s in stations if s.holder_id is None),
    })
