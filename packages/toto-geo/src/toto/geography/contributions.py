"""Members' contributions to a community: pins, zones and the comments under
them (stage 64, 2026-10-06), under the charged-door rule (``charging``).

A PIN is an ``Address`` and a ``CommunityPin``; a ZONE is a ``Zone`` and a
``CommunityZone``. Creating one charges ``geography.pin`` or
``geography.zone`` once; a later change by its author charges
``geography.note``; a change of nothing charges nothing; deleting is free and
refunds nothing. A COMMENT charges ``geography.comment`` when it is written;
its author edits it free; withdrawing is free.

Every charged function here keeps the rule's order: the ``op`` is cleaned and
bound to the request, a known ``op`` is a replay (the same request) or 409
(another one), the afford check comes before anything is written, and the
rows, the usage event and the charge are ONE transaction (``charging.settle``
with the rows made inside it). A refused ledger leaves no row and no changed
text. Two creations sent at once under one ``op`` are settled twice over: by
the usage event's key, and by the unique ``(author, op_key)`` of the row.

WHO MAY is not asked here: every caller is a door that has asked ``access``
first. What is checked here is what the request holds.

Nothing here writes a coordinate, an outline or a comment's text into the
audit chain, a usage event or the ledger.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import audit, charging, metrics, shapes
from .charging import Refusal
from .models import Address, CommunityPin, CommunityZone, PinComment, Zone, ZoneComment

NAME_MAX = 200
NOTE_MAX = 2000

PIN_LABEL = "Saved point"
ZONE_LABEL = "Saved zone"
CHANGE_LABEL = "Changed point or zone"
COMMENT_LABEL = "Comment on a pin or zone"


class _Raced(Exception):
    """Another request wrote first; nothing of this one may be kept."""


def _text(value, limit, what, required=False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Refusal(_("%(what)s must be text.") % {"what": what}, 400)
    if not charging.storable(value):
        raise Refusal(_("%(what)s holds a character that cannot be kept.") % {"what": what},
                      400)
    text = value.strip()
    if len(text) > limit:
        raise Refusal(_("%(what)s is too long: at most %(n)d characters.")
                      % {"what": what, "n": limit}, 400)
    if required and not text:
        raise Refusal(_("Give it a name."), 400)
    return text


def _hold(row) -> bool:
    """Take ``row`` until this transaction ends (a plain read on SQLite).
    False when it is gone."""
    rows = type(row)._base_manager.select_for_update(
        no_key=connection.features.has_select_for_no_key_update)
    return bool(list(rows.filter(pk=row.pk).values_list("pk", flat=True)))


# ---------------------------------------------------------------------------
# What a row is, as the page is handed it
# ---------------------------------------------------------------------------

def pin_state(pin) -> dict:
    lat, lng = shapes.pair_of(pin.address.point)
    return {"uid": str(pin.uid), "lat": lat, "lng": lng, "name": pin.address.name,
            "postal_address": pin.address.postal_address, "note": pin.address.note}


def zone_state(row) -> dict:
    return {"uid": str(row.uid), "name": row.zone.name,
            "description": row.zone.description,
            "outline": shapes.corners_of(row.zone.outline)}


def _clean_pin(lat, lng, name, postal_address, note) -> dict:
    try:
        lat, lng = shapes.clean_pair(lat, lng)
    except shapes.BadShape as exc:
        raise Refusal(str(exc), 400) from None
    return {"lat": lat, "lng": lng,
            "name": _text(name, NAME_MAX, _("The name"), required=True),
            "postal_address": _text(postal_address, NOTE_MAX, _("The address")),
            "note": _text(note, NOTE_MAX, _("The note"))}


def _clean_zone(name, description, outline):
    try:
        corners = shapes.clean_outline(outline)
        polygon = shapes.polygon_of(corners)
    except shapes.BadShape as exc:
        raise Refusal(str(exc), 400) from None
    return ({"name": _text(name, NAME_MAX, _("The name"), required=True),
             "description": _text(description, NOTE_MAX, _("The description")),
             "outline": [list(corner) for corner in corners]}, polygon)


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------

def _create(user, community, model, metric, label, action, op, body, make):
    """One contribution made, kept and charged once. ``make()`` writes the
    geometry row and answers the link row's keyword for it."""

    def made():
        return (model.objects.select_related("community", "author")
                .filter(author=user, op_key=op).first())

    def replay():
        row = made()
        if row is None:      # made under this op, and deleted since
            raise Refusal(_("This request was already used. Press the button again "
                            "to send a new one."), 409)
        return row, False

    if charging.known(user, op, body):
        return replay()
    charging.afford(user, metric)

    def save():
        try:
            with transaction.atomic():
                return model.objects.create(community=community, author=user, op_key=op,
                                            **make())
        except IntegrityError:
            raise _Raced() from None

    try:
        row, charged = charging.settle(user, metric, op, body, label, save=save)
    except charging.Duplicate:
        return replay()
    except _Raced:
        if charging.known(user, op, body):
            return replay()
        raise Refusal(_("This was changed by another request at the same moment. "
                        "Press the button again."), 409) from None
    audit.record(action, user, metric=metric, community=community,
                 amount=charging.amount_of(charged), outcome="charged", link=row)
    return row, True


def create_pin(user, community, *, lat, lng, name, postal_address, note, op):
    """``(pin, charged)``. Charges ``geography.pin``."""
    data = _clean_pin(lat, lng, name, postal_address, note)
    op = charging.clean_op(op)
    body = {"community": community.pk, "pin": data}

    def make():
        return {"address": Address.objects.create(
            point=shapes.point_of(data["lat"], data["lng"]), name=data["name"],
            postal_address=data["postal_address"], note=data["note"], created_by=user)}

    return _create(user, community, CommunityPin, metrics.PIN, PIN_LABEL,
                   audit.PIN_CREATED, op, body, make)


def create_zone(user, community, *, name, description, outline, op):
    """``(zone row, charged)``. Charges ``geography.zone``."""
    data, polygon = _clean_zone(name, description, outline)
    op = charging.clean_op(op)
    body = {"community": community.pk, "zone": data}

    def make():
        return {"zone": Zone.objects.create(
            name=data["name"], description=data["description"], outline=polygon,
            created_by=user)}

    return _create(user, community, CommunityZone, metrics.ZONE, ZONE_LABEL,
                   audit.ZONE_CREATED, op, body, make)


# ---------------------------------------------------------------------------
# Changing, by the author
# ---------------------------------------------------------------------------

def _edit(user, row, current, data, action, op, body, write, fresh):
    """One change kept and charged ``geography.note`` once; a change of
    nothing is free. ``write()`` updates the geometry row and answers how
    many rows it changed; ``fresh()`` reads the row again."""
    try:
        with transaction.atomic():
            if not _hold(row):
                raise Refusal(_("That is no longer on the map."), 404)
            if charging.known(user, op, body) or current() == data:
                return fresh(), False
            charging.afford(user, metrics.NOTE)

            def save():
                if not write():
                    raise _Raced()
                return row

            try:
                _, charged = charging.settle(user, metrics.NOTE, op, body, CHANGE_LABEL,
                                             save=save)
            except charging.Duplicate:
                return fresh(), False
    except _Raced:
        raise Refusal(_("That is no longer on the map."), 404) from None
    audit.record(action, user, metric=metrics.NOTE, community=row.community,
                 amount=charging.amount_of(charged), outcome="charged", link=row)
    return fresh(), True


def edit_pin(user, pin, *, name, postal_address, note, op, lat=None, lng=None):
    """The author's change to a pin. Where it stays unless both coordinates
    are sent."""
    if lat is None and lng is None:
        lat, lng = shapes.pair_of(pin.address.point)
    data = _clean_pin(lat, lng, name, postal_address, note)
    op = charging.clean_op(op)
    body = {"pin": pin.pk, "edit": data}

    def fresh():
        return CommunityPin.objects.select_related("community", "author", "address").get(
            pk=pin.pk)

    def current():
        state = pin_state(fresh())
        state.pop("uid")
        return state

    def write():
        return Address.objects.filter(pk=pin.address_id).update(
            point=shapes.point_of(data["lat"], data["lng"]), name=data["name"],
            postal_address=data["postal_address"], note=data["note"],
            updated_at=timezone.now())

    return _edit(user, pin, current, data, audit.PIN_EDITED, op, body, write, fresh)


def edit_zone(user, row, *, name, description, op, outline=None):
    """The author's change to a zone. Its outline stays unless one is sent."""
    if outline is None:
        outline = shapes.corners_of(row.zone.outline)
    data, polygon = _clean_zone(name, description, outline)
    op = charging.clean_op(op)
    body = {"zone": row.pk, "edit": data}

    def fresh():
        return CommunityZone.objects.select_related("community", "author", "zone").get(
            pk=row.pk)

    def current():
        state = zone_state(fresh())
        state.pop("uid")
        return state

    def write():
        return Zone.objects.filter(pk=row.zone_id).update(
            name=data["name"], description=data["description"], outline=polygon,
            updated_at=timezone.now())

    return _edit(user, row, current, data, audit.ZONE_EDITED, op, body, write, fresh)


# ---------------------------------------------------------------------------
# Deleting, hiding, restoring: free
# ---------------------------------------------------------------------------

def _is_pin(row) -> bool:
    return isinstance(row, CommunityPin)


def delete(user, row) -> bool:
    """Delete a contribution: its geometry row, its link and its comments.
    Free; nothing is refunded."""
    is_pin = _is_pin(row)
    with transaction.atomic():
        if not _hold(row):
            return False
        audit.record(audit.PIN_DELETED if is_pin else audit.ZONE_DELETED, user,
                     community=row.community, outcome="removed", link=row)
        # The link first, so that its comments go through their receiver;
        # the geometry row goes by the link's own receiver, and once more
        # here in case no signal ran.
        type(row).objects.filter(pk=row.pk).first().delete()
        if is_pin:
            Address.objects.filter(pk=row.address_id).delete()
        else:
            Zone.objects.filter(pk=row.zone_id).delete()
    return True


def hide(user, row) -> bool:
    """A moderator hides a contribution: it stays in the database, shown to
    its author (with a line saying so) and to the moderators only."""
    changed = type(row).objects.filter(pk=row.pk, hidden_at__isnull=True).update(
        hidden_at=timezone.now(), hidden_by=user)
    if changed:
        audit.record(audit.PIN_HIDDEN if _is_pin(row) else audit.ZONE_HIDDEN, user,
                     community=row.community, outcome="hidden", link=row)
    return bool(changed)


def restore(user, row) -> bool:
    changed = type(row).objects.filter(pk=row.pk, hidden_at__isnull=False).update(
        hidden_at=None, hidden_by=None)
    if changed:
        audit.record(audit.PIN_RESTORED if _is_pin(row) else audit.ZONE_RESTORED, user,
                     community=row.community, outcome="restored", link=row)
    return bool(changed)


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def _links(row):
    return PinComment if _is_pin(row) else ZoneComment


def comments_of(row):
    """The comments under a pin or zone, oldest first."""
    from toto.comments.models import Comment

    return (Comment.objects.select_related("author")
            .filter(pk__in=row.comment_links.values("comment_id")))


def comment_of(row, comment_pk):
    """The comment ``comment_pk`` if it is under ``row``, else None."""
    link = (row.comment_links.select_related("comment", "comment__author")
            .filter(comment_id=comment_pk).first())
    return link.comment if link is not None else None


def add_comment(user, row, *, body, reply_to, op) -> bool:
    """Write a comment under a pin or zone; True when it was charged
    (``geography.comment``), False for a replay. A reply answers a top-level
    comment under the same row: a reply to a reply is refused, because the
    thread shows one level."""
    from toto.comments import services

    text = _text(body, services.MAX_BODY, _("The comment"))
    if not text:
        raise Refusal(_("A comment needs some text."), 400)
    parent = None
    if reply_to not in (None, ""):
        try:
            parent = comment_of(row, int(reply_to))
        except (TypeError, ValueError):
            parent = None
        if parent is None:
            raise Refusal(_("That comment is not under this pin or zone."), 404)
        if parent.reply_to_id is not None:
            raise Refusal(_("A reply answers a comment, not another reply."), 400)
        if parent.is_deleted:
            raise Refusal(_("That comment was withdrawn; it cannot be answered."), 400)
    op = charging.clean_op(op)
    kind = "pin" if _is_pin(row) else "zone"
    digest_body = {kind: row.pk, "comment": text, "reply_to": parent.pk if parent else None}
    if charging.known(user, op, digest_body):
        return False
    charging.afford(user, metrics.COMMENT)

    def save():
        try:
            comment = services.add(user, text, reply_to=parent)
        except ValidationError as exc:
            raise Refusal(" ".join(exc.messages), 400) from None
        return _links(row).objects.create(comment=comment, **{kind: row})

    try:
        link, charged = charging.settle(user, metrics.COMMENT, op, digest_body,
                                        COMMENT_LABEL, save=save)
    except charging.Duplicate:
        return False
    audit.record(audit.COMMENT_CREATED, user, metric=metrics.COMMENT,
                 community=row.community, amount=charging.amount_of(charged),
                 outcome="charged", link=link)
    return True


def edit_comment(user, row, comment, body) -> None:
    """The author's change to their own comment. Free. The door has checked
    that ``user`` is the author; this asks it once more, on purpose."""
    from toto.comments import services

    if comment.author_id is None or comment.author_id != user.pk:
        raise Refusal(_("Only its author changes a comment."), 403)
    text = _text(body, services.MAX_BODY, _("The comment"))
    try:
        services.edit(comment, user, text)
    except ValidationError as exc:
        raise Refusal(" ".join(exc.messages), 400) from None
    audit.record(audit.COMMENT_EDITED, user, community=row.community, outcome="edited",
                 link=row.comment_links.filter(comment=comment).first())


def withdraw_comment(user, row, comment) -> None:
    """Withdraw a comment: its text stays in the database, unshown. The door
    has decided who may (the author, or a moderator of the community)."""
    from toto.comments import services

    if comment.is_deleted:
        return
    services.soft_delete(comment, user, by_moderator=True)
    audit.record(audit.COMMENT_WITHDRAWN, user, community=row.community,
                 outcome="withdrawn", link=row.comment_links.filter(comment=comment).first())
