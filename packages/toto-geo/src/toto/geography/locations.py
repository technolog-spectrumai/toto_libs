"""The Locations app (stage 64, 2026-10-06): one page for every signed-in
member, ``GET /geography/``, showing together everything on the map this
member may see, and the doors of members' contributions.

The owner: "Restore one shared Locations app, like the earlier version,
showing all geographic data together. Place its tile in the dashboard's Basic
tab and make it available to every signed-in user." - "'All data together'
means all addresses, pins and zones the current user may access. Preserve
community permissions and profile-location privacy." - "Route search exists
exclusively in the Locations app."

WHAT THE PAGE IS HANDED is ``access.visible_to(request.user)`` and nothing
else; every door that names a row asks the same object, so a list and a door
cannot disagree. Opening the page, a row or a thread charges nothing.

THE DOORS, each with its mark (``geography_door``; a walk over the URLconf
fails on a route without one):

    signed-in      the page
    community      create a pin or a zone in a community: its members and
                   an administrator; 403 for everybody else
    contribution   a pin or zone named by its uid: 404 when there is none
                   (or none under that community's slug), 403 for who does
                   not belong to its community, 404 for a hidden row the
                   member may not see. Then the act's own rule: edit is the
                   author; delete is the author or a moderator; hide and
                   restore are a moderator; a comment is edited by its author
                   and withdrawn by its author or a moderator. 403 otherwise.
    author         one's own rows, member of the community or not: the list
                   at ``me/contributions/``, deleting one's pin or zone,
                   withdrawing one's comment

NO PAGE DRAWS A COMMUNITY ZONE (the owner, 2026-10-06: "remove 'draw
community zone' from general locations tab"). The Locations page shows the
zones that exist and lets their author change their words; the door that
creates one (``communities/<slug>/zones/``) still stands and still asks for
a member of the community, and no page of the platform calls it.

Search nearby has no door: it is worked out in the page from the rows the
page already holds, so a centre is never sent anywhere. A route's ends are
sent, as coordinates only, to stage 63's route door, and nothing of a route
is kept.
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from . import access, contributions, places, shapes
from .charging import Refusal
from .models import CommunityPin, CommunityZone
from .views import _answer, _refuse, door

PIN, ZONE = "pin", "zone"
_MODEL = {PIN: CommunityPin, ZONE: CommunityZone}


def _marked(mark):
    def decorate(view):
        view.geography_door = mark
        return view
    return decorate


def form_door(mark):
    """A door a form of the page posts to: POST, a member, form fields in,
    JSON out. Refusals as JSON, as ``views.door`` answers them."""

    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method != "POST":
                response = _refuse(_("This address takes POST only."), 405)
                response["Allow"] = "POST"
                return response
            if not request.user.is_authenticated:
                return _refuse(_("Sign in to use the map."), 403)
            try:
                return _answer(view(request, *args, **kwargs))
            except places.REFUSALS as exc:
                return _refuse(exc, getattr(exc, "status_code", 400),
                               getattr(exc, "retry_after", None))

        wrapped.geography_door = mark
        return wrapped

    return decorate


# ---------------------------------------------------------------------------
# Who asks
# ---------------------------------------------------------------------------

def _community(request, slug):
    """The community, for who may see and add to its contributions: 404 for
    a slug that names none, 403 for who does not belong to it."""
    from toto.socialhub.models import Community

    community = Community.objects.filter(slug=slug).first()
    if community is None:
        raise Refusal(_("Community not found."), 404)
    if not access.may_contribute(request.user, community):
        raise Refusal(_("Only this community's members see and add to its pins and zones."),
                      403)
    return community


def _contribution(request, kind, uid, slug=None):
    """``(visible, row)`` for the pin or zone ``uid``; see the module's
    docstring for the order of the refusals."""
    gone = Refusal(_("That is not on the map."), 404)
    known = _MODEL[kind].objects.select_related("community").filter(uid=uid).first()
    if known is None or (slug is not None and known.community.slug != slug):
        raise gone
    if not access.may_contribute(request.user, known.community):
        raise Refusal(_("Only this community's members see and add to its pins and zones."),
                      403)
    visible = access.visible_to(request.user)
    row = visible.pin(uid) if kind == PIN else visible.zone(uid)
    if row is None:
        raise gone
    return visible, row


def _own(request, kind, uid):
    """The member's own pin or zone, in whatever community: 404 otherwise."""
    geometry = "address" if kind == PIN else "zone"
    row = (_MODEL[kind].objects.select_related("community", geometry)
           .filter(uid=uid, author=request.user).first())
    if row is None:
        raise Http404("No such contribution of yours.")
    return row


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def _username(user) -> str:
    return user.get_username() if user is not None else ""


def _community_of(community) -> dict:
    return {"slug": community.slug, "name": str(community.name)}


def rows_for(visible, community=None) -> tuple[list, bool]:
    """Every row ``visible`` holds, as the page draws it, and whether a kind
    was cut at ``access.ROW_CAP``. With ``community`` only that community's
    headquarters, zone and contributions, and no person's point.

    A row: ``{id, kind, name, note, lat, lng | outline, …}``. ``id`` is the
    page's own name for the row and holds no database id. Every text is
    drawn as text by the page (``locations.js``), never as markup."""
    cap = access.ROW_CAP
    rows, capped = [], False

    def take(queryset):
        nonlocal capped
        found = list(queryset[:cap + 1])
        if len(found) > cap:
            capped = True
        return found[:cap]

    if community is None:
        for link in take(visible.people().order_by("person__display_name")):
            lat, lng = shapes.pair_of(link.address.point)
            own = link.person.user_id == visible.user.pk
            rows.append({
                "id": f"person:{link.person.slug}", "kind": "person",
                "name": str(link.person.display_name or ""),
                "detail": link.address.name,
                # A person's note is their own: shown to them only.
                "note": link.address.note if own else "",
                "lat": lat, "lng": lng, "own": own,
                "link": reverse("socialhub:profile_details",
                                kwargs={"slug": link.person.slug}),
            })
    headquarters = visible.headquarters().order_by("community__name")
    if community is not None:
        headquarters = headquarters.filter(community=community)
    for link in take(headquarters):
        about = _community_of(link.community)
        page = reverse("socialhub:community_detail", kwargs={"slug": link.community.slug})
        if link.address is not None:
            lat, lng = shapes.pair_of(link.address.point)
            rows.append({"id": f"headquarters:{about['slug']}", "kind": "headquarters",
                         "name": link.address.name or about["name"],
                         "note": link.address.note, "lat": lat, "lng": lng,
                         "community": about, "link": page})
        if link.zone is not None:
            rows.append({"id": f"area:{about['slug']}", "kind": "area",
                         "name": link.zone.name or about["name"],
                         "note": link.zone.description,
                         "outline": shapes.corners_of(link.zone.outline),
                         "community": about, "link": page})
    pins, zones = visible.pins(), visible.zones()
    if community is not None:
        pins, zones = pins.filter(community=community), zones.filter(community=community)
    for pin in take(pins):
        lat, lng = shapes.pair_of(pin.address.point)
        rows.append({**_contribution_row(visible, pin, PIN), "name": pin.address.name,
                     "note": pin.address.note, "postal_address": pin.address.postal_address,
                     "lat": lat, "lng": lng})
    for zone in take(zones):
        rows.append({**_contribution_row(visible, zone, ZONE), "name": zone.zone.name,
                     "note": zone.zone.description,
                     "outline": shapes.corners_of(zone.zone.outline)})
    return rows, capped


def _contribution_row(visible, row, kind) -> dict:
    slug = row.community.slug
    names = {"slug": slug, "uid": row.uid}
    return {
        "id": f"{kind}:{row.uid}", "kind": kind, "uid": str(row.uid),
        "community": _community_of(row.community), "author": _username(row.author),
        "hidden": row.is_hidden,
        "may_edit": access.may_edit(visible.user, row),
        "may_moderate": visible.moderates(row.community_id),
        "urls": {
            "detail": reverse(f"geography:{kind}_detail", kwargs=names),
            "delete": reverse(f"geography:{kind}_delete", kwargs=names),
            "hide": reverse(f"geography:{kind}_hide", kwargs=names),
            "restore": reverse(f"geography:{kind}_restore", kwargs=names),
        },
    }


def _texts() -> dict:
    return {
        "failed": _("That did not work. Try again."),
        "kinds": {"person": _("People"), "headquarters": _("Headquarters"),
                  "area": _("Community areas"), "pin": _("Community pins"),
                  "zone": _("Community zones"), "temporary": _("Temporary point"),
                  "hit": _("Search result")},
        "hidden": _("Hidden by a moderator"),
        "nothing": _("Nothing matches."),
        "shown": _("%(n)s shown"),
        "near": _("%(n)s within %(km)s km"),
        "km_away": _("%(km)s km away"),
        "choose_centre": _("Choose a centre first: click the map, or open a row."),
        "centre_is": _("Centre: %(name)s"),
        "clicked": _("The point you clicked"),
        "choose_community": _("Choose a community"),
        "no_community": _("You belong to no community yet, so there is nowhere to save a "
                          "pin."),
        "saved": _("Saved."),
        "confirm_delete": _("Delete this for good? Its comments go with it."),
        "by": _("by %(name)s"),
        "former_member": _("a former member"),
        "open_profile": _("Open the profile"),
        "open_community": _("Open the community"),
        "yours_note": _("Your own point. Other members see it only while \"show address\" "
                        "is on."),
        "keep_hit": _("Keep on the map"),
        "all_communities": _("All communities"),
        "n_communities": _("%(n)s communities"),
        "no_communities": _("There is no community to filter by yet."),
        "advanced": _("Advanced"),
        "advanced_hidden": _("Advanced · %(n)s hidden"),
        "advanced_count": _("%(n)s of %(m)s chosen"),
    }


#: The tabs beside the map, in their order. ``route`` is there only where
#: the page has route search.
TOOLS = ("index", "nearby", "route")


def _tool(request, routed: bool) -> str:
    """The tab the address asks for (``?tool=nearby``, ``?tool=route``), so
    that a reload and a link keep it; the index for anything else, and for
    ``route`` on a page with no route search."""
    asked = request.GET.get("tool", "")
    if asked not in TOOLS or (asked == "route" and not routed):
        return TOOLS[0]
    return asked


def _community_filter(request) -> list[str]:
    """The communities the address ticks: ``?community=a&community=b``; the
    community page's link carries one. Each once, in the order given. The
    page ticks those it knows and drops the rest; the filter itself is
    worked out in the page, from the rows it was handed."""
    asked = []
    for slug in request.GET.getlist("community")[:200]:
        if slug and len(slug) <= 200 and slug not in asked:
            asked.append(slug)
    return asked


@_marked("signed-in")
@login_required
@require_safe
def page(request):
    """The Locations page. Free on every plan, and it charges nothing."""
    from toto.ui import PageProcessor

    from .mapview import map_context

    visible = access.visible_to(request.user)
    rows, capped = rows_for(visible)
    geo = map_context("geography-locations", routes=True)
    communities = [_community_of(community) for community in visible.communities()]
    for community in communities:
        slug = {"slug": community["slug"]}
        community["pins"] = reverse("geography:pin_create", kwargs=slug)
    counts = {kind: sum(1 for row in rows if row["kind"] == kind)
              for kind in ("person", "headquarters", "area", "pin", "zone")}
    config = {
        "rows": rows, "capped": capped, "cap": access.ROW_CAP,
        "communities": communities,
        "urls": geo["config"]["urls"],
        "texts": {**geo["config"]["texts"], **_texts()},
        "radii": [1, 5, 10, 25, 50, 100],
        "filter": _community_filter(request),
        "open": request.GET.get("open", ""),
    }
    context = {
        "geo": geo, "config": config, "counts": counts, "total": len(rows),
        "capped": capped, "cap": access.ROW_CAP, "communities": communities,
        "search_enabled": geo["search_enabled"], "route_modes": geo["route_modes"],
        "radii": config["radii"], "tool": _tool(request, bool(geo["route_modes"])),
    }
    return render(request, "geography/locations.html",
                  PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------

@door("community")
def pin_create(request, data, slug):
    community = _community(request, slug)
    pin, charged = contributions.create_pin(
        request.user, community, lat=data.get("lat"), lng=data.get("lng"),
        name=data.get("name"), postal_address=data.get("postal_address"),
        note=data.get("note"), op=data.get("op"))
    return {"pin": contributions.pin_state(pin), "charged": charged}


@door("community")
def zone_create(request, data, slug):
    community = _community(request, slug)
    zone, charged = contributions.create_zone(
        request.user, community, name=data.get("name"),
        description=data.get("description"), outline=data.get("outline"),
        op=data.get("op"))
    return {"zone": contributions.zone_state(zone), "charged": charged}


# ---------------------------------------------------------------------------
# One contribution: its details, and the author's change
# ---------------------------------------------------------------------------

def _details(request, kind, slug, uid):
    """The details of one pin or zone, as a piece of HTML for the page's
    panel: its texts (escaped by the template), the author's Change button
    (it holds the words the page's dialog starts from, and the door they are
    sent to), the moderator's buttons and the thread."""
    try:
        visible, row = _contribution(request, kind, uid, slug)
    except Refusal as exc:
        return HttpResponse(str(exc), status=exc.status_code,
                            content_type="text/plain; charset=utf-8")
    author = access.may_edit(request.user, row)
    moderator = visible.moderates(row.community_id)
    comments = list(contributions.comments_of(row))
    verdicts = {comment.pk: (comment.author_id == request.user.pk,
                             comment.author_id == request.user.pk or moderator)
                for comment in comments}
    names = {"slug": row.community.slug, "uid": row.uid}
    state = contributions.pin_state(row) if kind == PIN else contributions.zone_state(row)
    response = render(request, "geography/_details.html", {
        "kind": kind, "row": row, "state": state, "community": row.community,
        "author_name": _username(row.author), "may_edit": author,
        "may_moderate": moderator, "may_delete": author or moderator,
        "comments": comments, "verdicts": verdicts,
        "edit_url": reverse(f"geography:{kind}_detail", kwargs=names),
        "comment_url": reverse(f"geography:{kind}_comment_add", kwargs={"uid": row.uid}),
        "comment_edit": f"geography:{kind}_comment_edit",
        "comment_withdraw": f"geography:{kind}_comment_withdraw",
    })
    response["Cache-Control"] = "no-store"
    return response


@door("contribution")
def _pin_edit(request, data, slug, uid):
    _visible, pin = _contribution(request, PIN, uid, slug)
    if not access.may_edit(request.user, pin):
        raise Refusal(_("Only its author changes a pin."), 403)
    pin, charged = contributions.edit_pin(
        request.user, pin, name=data.get("name"),
        postal_address=data.get("postal_address"), note=data.get("note"),
        lat=data.get("lat"), lng=data.get("lng"), op=data.get("op"))
    return {"pin": contributions.pin_state(pin), "charged": charged}


@door("contribution")
def _zone_edit(request, data, slug, uid):
    _visible, zone = _contribution(request, ZONE, uid, slug)
    if not access.may_edit(request.user, zone):
        raise Refusal(_("Only its author changes a zone."), 403)
    zone, charged = contributions.edit_zone(
        request.user, zone, name=data.get("name"), description=data.get("description"),
        outline=data.get("outline"), op=data.get("op"))
    return {"zone": contributions.zone_state(zone), "charged": charged}


@_marked("contribution")
@login_required
def pin_detail(request, slug, uid):
    """GET: the pin's details. POST: the author's change (JSON, charged)."""
    if request.method in ("GET", "HEAD"):
        return _details(request, PIN, slug, uid)
    return _pin_edit(request, slug, uid)


@_marked("contribution")
@login_required
def zone_detail(request, slug, uid):
    if request.method in ("GET", "HEAD"):
        return _details(request, ZONE, slug, uid)
    return _zone_edit(request, slug, uid)


# ---------------------------------------------------------------------------
# Deleting and moderating: free
# ---------------------------------------------------------------------------

def _delete(request, kind, slug, uid):
    visible, row = _contribution(request, kind, uid, slug)
    if not (access.may_edit(request.user, row) or visible.moderates(row.community_id)):
        raise Refusal(_("Only its author or the community's head deletes this."), 403)
    return {"removed": contributions.delete(request.user, row)}


def _moderate(request, kind, slug, uid, act):
    visible, row = _contribution(request, kind, uid, slug)
    if not visible.moderates(row.community_id):
        raise Refusal(_("Only the community's head hides or restores this."), 403)
    return {"changed": act(request.user, row)}


@door("contribution")
def pin_delete(request, data, slug, uid):
    return _delete(request, PIN, slug, uid)


@door("contribution")
def zone_delete(request, data, slug, uid):
    return _delete(request, ZONE, slug, uid)


@door("contribution")
def pin_hide(request, data, slug, uid):
    return _moderate(request, PIN, slug, uid, contributions.hide)


@door("contribution")
def zone_hide(request, data, slug, uid):
    return _moderate(request, ZONE, slug, uid, contributions.hide)


@door("contribution")
def pin_restore(request, data, slug, uid):
    return _moderate(request, PIN, slug, uid, contributions.restore)


@door("contribution")
def zone_restore(request, data, slug, uid):
    return _moderate(request, ZONE, slug, uid, contributions.restore)


# ---------------------------------------------------------------------------
# Comments: the thread's forms post here; the page sends them and reads JSON
# ---------------------------------------------------------------------------

def _comment(request, kind, uid, pk):
    visible, row = _contribution(request, kind, uid)
    comment = contributions.comment_of(row, pk)
    if comment is None:
        raise Refusal(_("That comment is not under this pin or zone."), 404)
    return visible, row, comment


def _comment_add(request, kind, uid):
    _visible, row = _contribution(request, kind, uid)
    charged = contributions.add_comment(
        request.user, row, body=request.POST.get("body", ""),
        reply_to=request.POST.get("reply_to"), op=request.POST.get("op"))
    return {"ok": True, "charged": charged}


def _comment_edit(request, kind, uid, pk):
    _visible, row, comment = _comment(request, kind, uid, pk)
    # Its author and nobody else: not staff, not a moderator.
    if comment.author_id is None or comment.author_id != request.user.pk:
        raise Refusal(_("Only its author changes a comment."), 403)
    contributions.edit_comment(request.user, row, comment, request.POST.get("body", ""))
    return {"ok": True}


def _comment_withdraw(request, kind, uid, pk):
    visible, row, comment = _comment(request, kind, uid, pk)
    own = comment.author_id is not None and comment.author_id == request.user.pk
    if not (own or visible.moderates(row.community_id)):
        raise Refusal(_("Only its author or the community's head withdraws a comment."), 403)
    contributions.withdraw_comment(request.user, row, comment)
    return {"ok": True}


@form_door("contribution")
def pin_comment_add(request, uid):
    return _comment_add(request, PIN, uid)


@form_door("contribution")
def zone_comment_add(request, uid):
    return _comment_add(request, ZONE, uid)


@form_door("contribution")
def pin_comment_edit(request, uid, pk):
    return _comment_edit(request, PIN, uid, pk)


@form_door("contribution")
def zone_comment_edit(request, uid, pk):
    return _comment_edit(request, ZONE, uid, pk)


@form_door("contribution")
def pin_comment_withdraw(request, uid, pk):
    return _comment_withdraw(request, PIN, uid, pk)


@form_door("contribution")
def zone_comment_withdraw(request, uid, pk):
    return _comment_withdraw(request, ZONE, uid, pk)


# ---------------------------------------------------------------------------
# One's own rows, member or not
# ---------------------------------------------------------------------------

def own_comments(user):
    """``[(kind, row, comment)]``: the member's comments under pins and
    zones, withdrawn ones left out."""
    from .models import PinComment, ZoneComment

    found = []
    for kind, model in ((PIN, PinComment), (ZONE, ZoneComment)):
        geometry = "address" if kind == PIN else "zone"
        links = (model.objects
                 .select_related("comment", kind, f"{kind}__community", f"{kind}__{geometry}")
                 .filter(comment__author=user, comment__deleted_at__isnull=True)
                 .order_by("-comment__created_at"))
        found.extend((kind, getattr(link, kind), link.comment) for link in links)
    return found


@_marked("author")
@login_required
@require_safe
def my_contributions(request):
    """The member's own pins, zones and comments in every community, by
    name, community and date. No map, and nobody else's rows."""
    from toto.ui import PageProcessor

    user = request.user
    context = {
        "pins": CommunityPin.objects.select_related("community", "address")
        .filter(author=user),
        "zones": CommunityZone.objects.select_related("community", "zone")
        .filter(author=user),
        "comments": [
            {"kind": kind, "comment": comment, "community": row.community,
             "on": row.address.name if kind == PIN else row.zone.name}
            for kind, row, comment in own_comments(user)],
    }
    return render(request, "geography/contributions.html",
                  PageProcessor().decorate(context, request))


def _back(request, text):
    messages.success(request, text)
    return redirect("geography:my_contributions")


@_marked("author")
@login_required
@require_POST
def my_pin_delete(request, uid):
    contributions.delete(request.user, _own(request, PIN, uid))
    return _back(request, _("The pin was deleted."))


@_marked("author")
@login_required
@require_POST
def my_zone_delete(request, uid):
    contributions.delete(request.user, _own(request, ZONE, uid))
    return _back(request, _("The zone was deleted."))


@_marked("author")
@login_required
@require_POST
def my_comment_withdraw(request, pk):
    for _kind, row, comment in own_comments(request.user):
        if comment.pk == pk:
            contributions.withdraw_comment(request.user, row, comment)
            return _back(request, _("The comment was withdrawn."))
    raise Http404("No such comment of yours.")
