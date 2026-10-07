"""The dashboard's "Forum" section: one entry for each community whose
channel the member may open (the owner, 2026-10-07: "also add dashboard tab
: forum where user can see all foras for each community").

The list is ``access.communities_of(user)``, the very list the forum's own
page draws and the rule every door asks, so the dashboard and the doors
cannot disagree: every community the member belongs to (a member, a senior
member or its head), every community for an administrator, and no section
at all for who is not entitled (nobody on Free, nobody signed out).
COMMUNITIES are listed, not channels: a channel is made on its first
opening, so a community whose channel nobody has opened yet is here too.

Beside each name: the community's kind, how many messages its channel holds
and when the last one was posted. That is metadata, read in ONE query for
the whole list; nothing is unsealed, and no message text ever reaches the
dashboard. Two queries for the list however many communities there are.
"""

from __future__ import annotations

from django.db.models import Count, Max
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from toto.core.dashboard import DashboardSection

from .. import access


def entries_for(user) -> list[dict]:
    """``[{"community", "messages", "last"}]`` for the communities whose
    channel ``user`` may read, by name. Empty for who is not entitled."""
    from ..models import ForumMessage

    communities = list(access.communities_of(user))
    if not communities:
        return []
    stats = {
        row["channel__community_id"]: row
        for row in (ForumMessage.objects
                    .filter(channel__community__in=[c.pk for c in communities],
                            removed_at__isnull=True)
                    .values("channel__community_id")
                    .annotate(messages=Count("id"), last=Max("created_at"))
                    .order_by())
    }
    return [{"community": community,
             "messages": stats.get(community.pk, {}).get("messages", 0),
             "last": stats.get(community.pk, {}).get("last")}
            for community in communities]


def _describe(entry) -> str:
    kind = str(entry["community"].get_org_type_display() or "")
    count = entry["messages"]
    if not count:
        words = _("no messages yet")
    else:
        when = formats.date_format(timezone.localtime(entry["last"]), "SHORT_DATETIME_FORMAT")
        words = ngettext("%(count)d message, the last on %(when)s",
                         "%(count)d messages, the last on %(when)s",
                         count) % {"count": count, "when": when}
    return f"{kind}: {words}" if kind else words


@DashboardSection.plugin(key="forum", title=gettext_lazy("Forum"), order=20)
class ForumDashboardSection(DashboardSection):
    def section(self, request):
        user = getattr(request, "user", None)
        if not access.entitled(user):
            return None
        entries = entries_for(user)
        if not entries:
            return {"items": [],
                    "note": _("A forum belongs to a community, and you belong to none yet."),
                    "note_link": reverse("socialhub:community_list"),
                    "note_label": _("See the communities")}
        return {"items": [
            {"title": entry["community"].name, "description": _describe(entry),
             "icon": "fa-solid fa-hashtag",
             "link": reverse("forum:channel_detail", args=[entry["community"].slug])}
            for entry in entries]}
