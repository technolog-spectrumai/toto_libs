"""The doors of a community's organisation chart (2026-10-06, stage 66).

Each door, in this order: POST only (405); somebody signed in (403); the
community the address names (404); its head or an administrator (403:
``permissions.may_moderate_community``, never staff alone, never a senior
member); and, where the address names a position, a position of THAT
community (404 otherwise, another community's included). The chart's own
rules, the ring among them, are ``toto.socialhub.org_chart``'s; what it
refuses is said in a message and nothing changes. Every change is on the
audit chain (``audit.py``, ``SOCIALHUB.POSITION_*``).
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.utils.translation import gettext as _

from toto.socialhub import org_chart
from toto.socialhub.models import Community
from toto.socialhub.permissions import may_moderate_community


def door(view):
    @wraps(view)
    def wrapped(request, slug, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not request.user.is_authenticated:
            raise PermissionDenied
        community = Community.objects.filter(slug=slug).first()
        if community is None:
            raise Http404
        if not may_moderate_community(request.user, community):
            raise PermissionDenied
        return view(request, community, *args, **kwargs)

    wrapped.org_chart_door = True
    return wrapped


def _back(community):
    from toto.socialhub.views.community import community_page_url

    return HttpResponseRedirect(community_page_url(community, org_chart.TAB))


def _typed(request):
    """What the form sent: the position's name, the person by profile slug
    (empty for nobody), the superior by id (empty for none), the order."""
    from toto.people.models import Person

    slug = (request.POST.get("person") or "").strip()
    person = None
    if slug:
        person = Person.objects.filter(slug=slug).first()
        if person is None:
            raise org_chart.ChartError(_("Choose a person, or nobody for a vacant position."))
    return {"title": request.POST.get("title"), "person": person,
            "reports_to": (request.POST.get("reports_to") or "").strip(),
            "order": request.POST.get("order")}


@door
def position_create(request, community):
    try:
        org_chart.create(community, **_typed(request))
    except org_chart.ChartError as refusal:
        messages.error(request, str(refusal))
    else:
        messages.success(request, _("The position was added."))
    return _back(community)


@door
def position_edit(request, community, pk):
    try:
        position = org_chart.change(community, pk, **_typed(request))
    except org_chart.ChartError as refusal:
        messages.error(request, str(refusal))
        return _back(community)
    if position is None:
        raise Http404
    messages.success(request, _("The position was changed."))
    return _back(community)


@door
def position_delete(request, community, pk):
    if not org_chart.delete(community, pk):
        raise Http404
    messages.success(request, _("The position was removed."))
    return _back(community)
