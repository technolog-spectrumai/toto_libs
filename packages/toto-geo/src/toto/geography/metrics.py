"""What geography meters. Pure data: no models, no database, no settings.

Two questions the server puts to an outside service, charged compute mana
when they are answered: a place-name search and a route. Three things kept,
charged storage mana once: a saved point, a saved zone, and a later change to
either (a note typed when the point or zone is first saved is covered by its
own price). Looking at a map, placing a temporary point and removing what
was saved are free. Since stage 64 a comment under a community's pin or zone is
a fourth thing kept. ``charging`` says when a charge is made and when not.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

APP = "geography"
LOOKUP = "geography.lookup"
ROUTE = "geography.route"
PIN = "geography.pin"
ZONE = "geography.zone"
NOTE = "geography.note"
COMMENT = "geography.comment"

registry.register(Metric(
    code=LOOKUP, label=_("Place search"), app_label=APP, unit="lookup",
    description=_("One place name looked up on the server."),
    default_limit=200,
))
registry.register(Metric(
    code=ROUTE, label=_("Route search"), app_label=APP, unit="route",
    description=_("One route calculated between two points. It is not kept."),
    default_limit=100,
))
registry.register(Metric(
    code=PIN, label=_("Saved point"), app_label=APP, unit="point",
    description=_("One point saved on the map: your own address or a community's headquarters."),
    default_limit=100,
))
registry.register(Metric(
    code=ZONE, label=_("Saved zone"), app_label=APP, unit="zone",
    description=_("One zone saved on the map."),
    default_limit=100,
))
registry.register(Metric(
    code=NOTE, label=_("Changed point or zone"), app_label=APP, unit="change",
    description=_("One later change to a saved point or zone."),
    default_limit=200,
))
registry.register(Metric(
    code=COMMENT, label=_("Comment on a pin or zone"), app_label=APP, unit="comment",
    description=_("One comment written under a community's pin or zone."),
    default_limit=200,
))

#: Every metric of this app, for the lookup of a known ``op``.
ALL = (LOOKUP, ROUTE, PIN, ZONE, NOTE, COMMENT)
UNIT = {LOOKUP: "lookup", ROUTE: "route", PIN: "point", ZONE: "zone", NOTE: "change",
        COMMENT: "comment"}
