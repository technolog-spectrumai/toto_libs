"""The Monitoring strip: one operator destination, four tabs, two wheels.

Monitoring, Database and Audit were three dashboard tiles until 2026-09-01, and
History was a section at the bottom of the Monitoring page. They are one
destination now, and this module is its table of contents — built the way
`tools.py` builds the Tools strip: tabs as data, apps named as STRINGS for
`apps.is_installed`, URLs reversed lazily and dropped on NoReverseMatch, so an
uninstalled or unmounted app offers nothing rather than 500ing the strip.

This lives in toto.core and not in toto.monit for the dependency direction:
the strip renders on the Audit page too, audit ships in toto-base, and
toto-base may not import toto-ops. toto.monit imports THIS, which is the way
that arrow already points.

THE GATES ARE PER TAB, and that is the one thing this strip does that the
Tools strip does not. monit's pages are superuser-only (MonitAccessMixin raises 403);
audit's are staff (staff_member_required redirects). The strip shows each
viewer only the tabs their role can open — the forum's hide-don't-refuse
convention — so a staff-not-superuser sees a one-tab strip rather than three
refusals. "staff" here means is_staff OR is_superuser, exactly as the
dashboard's visibility rule reads it (core/views.py `_resolve_dashboard_item`);
Django's is_superuser does not imply is_staff, and a superuser without the
staff bit must not lose the Audit tab.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class MonitoringTab:
    """One tab of the merged Monitoring destination."""

    slug: str
    label: str
    icon: str
    url_name: str
    app_label: str
    gate: str  # "superuser" | "staff"
    blurb: str = ""


TABS: tuple = (
    MonitoringTab(slug="monitoring", label="Monitoring",
                  icon="fa-solid fa-heart-pulse",
                  url_name="monit:overview", app_label="toto.monit",
                  gate="superuser",
                  blurb="This host, right now: system, services, requests."),
    MonitoringTab(slug="database", label="Database",
                  icon="fa-solid fa-stethoscope",
                  url_name="monit:status", app_label="toto.monit",
                  gate="superuser",
                  blurb="Record health: database, migrations, backups, the "
                        "audit chain."),
    MonitoringTab(slug="audit", label="Audit",
                  icon="fa-solid fa-clipboard-check",
                  url_name="audit:index", app_label="toto.audit",
                  gate="staff",
                  blurb="The hash-chained record of what happened here."),
    MonitoringTab(slug="history", label="History",
                  icon="fa-solid fa-chart-line",
                  url_name="monit:history", app_label="toto.monit",
                  gate="superuser",
                  blurb="Sampled machine trends over the last 48 hours."),
)


def _may_open(user, gate: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if gate == "superuser":
        return bool(user.is_superuser)
    # "staff" is the dashboard's reading: is_staff OR is_superuser.
    return bool(user.is_staff or user.is_superuser)


def monitoring_tabs(user, active: str = "") -> list:
    """THE strip — every tab this host serves that this viewer may open.

    `active` is a SLUG, not a URL, for the reason tools_tabs gives: the tabs
    live in two different apps under two different prefixes, so there is no one
    request path to compare against — the page that renders the strip is the
    only thing that knows which tab it is.
    """
    from django.apps import apps as django_apps
    from django.urls import NoReverseMatch, reverse

    out = []
    for tab in TABS:
        if not _may_open(user, tab.gate):
            continue
        if not django_apps.is_installed(tab.app_label):
            continue
        try:
            url = reverse(tab.url_name)
        except NoReverseMatch:
            # Installed but unmounted — an offer that leads nowhere is worse
            # than no offer.
            continue
        out.append({"slug": tab.slug, "label": tab.label, "icon": tab.icon,
                    "url": url, "blurb": tab.blurb,
                    "active": tab.slug == active})
    return out
