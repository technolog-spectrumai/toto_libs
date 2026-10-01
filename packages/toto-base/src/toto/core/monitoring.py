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
Tools strip does not. monit's pages are for a superuser on the Superuser plan
(MonitAccessMixin raises 403 — `superuser_on_plan` below, since 2026-10-01);
audit's are staff (staff_member_required redirects). The strip shows each
viewer only the tabs their role can open — the forum's hide-don't-refuse
convention — so a staff-not-superuser, or a superuser who has not taken the
plan, sees a one-tab strip rather than four refusals. "staff" here means
is_staff OR is_superuser, exactly as the dashboard's visibility rule reads it
(core/views.py `_resolve_dashboard_item`); Django's is_superuser does not
imply is_staff, and a superuser without the staff bit must not lose the Audit
tab.
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
    MonitoringTab(slug="jobs", label="Jobs",
                  icon="fa-solid fa-list-check",
                  url_name="monit:jobs", app_label="toto.monit",
                  gate="superuser",
                  blurb="What the background workers have been doing: queued, "
                        "running, finished and failed."),
    MonitoringTab(slug="history", label="History",
                  icon="fa-solid fa-chart-line",
                  url_name="monit:history", app_label="toto.monit",
                  gate="superuser",
                  blurb="Sampled machine trends over the last 48 hours."),
)


def superuser_on_plan(user) -> bool:
    """Who may open monit's pages: a superuser on the Superuser plan — both,
    never one (`toto.subscriptions.models.superuser_plan_active`).

    The privilege alone opened them until 2026-10-01, the one superuser
    function left that did not ask for the plan (36.R2). MonitAccessMixin and
    the strip's "superuser" tabs both ask this, so a tab is never shown to
    someone its page would refuse. A host without toto.subscriptions sells no
    plan, and there being a superuser is enough — the dashboard's rule
    (`core.views._superuser_with_plan`), without its open door on an error:
    this one is a gate, not a tile.
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_superuser", False):
        return False
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.subscriptions"):
        return True
    from toto.subscriptions.models import superuser_plan_active

    return superuser_plan_active(user)


def _may_open(user, gate: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if gate == "superuser":
        return superuser_on_plan(user)
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

    # Asked once per gate, not per tab: the "superuser" gate reads the
    # viewer's plan from the database, and four tabs share it.
    may_open = {gate: _may_open(user, gate) for gate in {tab.gate for tab in TABS}}
    out = []
    for tab in TABS:
        if not may_open[tab.gate]:
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
