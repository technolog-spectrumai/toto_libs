"""A community's seat on the map: the one door (2026-10-12).

The seat was a key on ``socialhub.Community`` set in the admin panel until
2026-10-04; it is this app's ``CommunitySeat`` row now, and its form is the
section this app draws on the community's page
(``plugins/community_plugins.py``). The place is one of the registered ones
the person may read (``links.registered_places``); the territory one they
may read. Floats and keys: no geometry is asked for here.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import links

SECTION = "#seat-on-the-map"


def _chosen(queryset, raw):
    """``(row or None, ok)``: the row ``raw`` names in ``queryset``; an empty
    value is no row and fine, anything else that is not there is not."""
    raw = (raw or "").strip()
    if not raw:
        return None, True
    if not raw.isdigit():
        return None, False
    row = queryset.filter(pk=int(raw)).first()
    return row, row is not None


@login_required
@require_POST
def set_community_seat(request, slug):
    from toto.socialhub.models import Community

    from .access import readable_territories

    community = get_object_or_404(Community, slug=slug)
    if not links.may_set_seat(request.user, community):
        return HttpResponseForbidden(_("The seat is set by the community's head or an administrator."))
    page = reverse("socialhub:community_detail", args=[community.slug]) + SECTION
    address, found = _chosen(links.registered_places(request.user), request.POST.get("address"))
    if not found:
        messages.error(request, _("That place is not one of the registered ones."))
        return redirect(page)
    territory, found = _chosen(readable_territories(request.user), request.POST.get("territory"))
    if not found:
        messages.error(request, _("That territory is not one you may choose."))
        return redirect(page)
    links.set_community_seat(community, address, territory)
    # The page's own line for the seat is text (Community.seat): written
    # from the place where nobody has typed one, never over what is there.
    if address is not None and not (community.seat or "").strip():
        community.seat = str(address)[:255]
        community.save(update_fields=["seat"])
    if address is None and territory is None:
        messages.success(request, _("The community has no seat on the map now."))
    else:
        messages.success(request, _("The community's seat on the map was saved."))
    return redirect(page)
