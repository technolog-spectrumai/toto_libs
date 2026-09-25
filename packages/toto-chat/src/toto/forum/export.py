"""The whole forum as a folder of web pages, in a ZIP.

The archive is a small static site, not a data dump: one page per room with the
messages in daily sections, one index linking to them, the images and voice
recordings beside them, and every link relative — so unzipping it anywhere and
opening `index.html` works, offline, on a machine that has never heard of this
platform.

## Why it streams, and why it surveys first

The response is a `StreamingHttpResponse`, so the bytes leave as they are made
rather than piling up in one worker. The price is that **a streaming response
cannot change its status once it has begun**: by the time we know an export is
too big, the client already has a 200. So there are two passes. The first
counts everything, measures every blob and decides whether to refuse; only then
does the second render and yield. That is the survey's whole job.

## What it is honest about

* **A staff export crosses every room**, including private ones the person
  running it never joined. The page says so, and so does the archive.
* **Deleted messages are tombstones** — sender and time, never the body and
  never the file. An export that quietly restored what somebody deleted would
  be the one surface in the platform that undoes a deletion; a silent gap would
  be indistinguishable from a message never sent, or from a bug in here.
* **Files that vanished or changed under us are named, not hidden.** The forum
  is live and cleanup deletes exactly these blobs, so pass two re-checks each
  one immediately before reading it and records what it skipped in
  `manifest.json` and on the index page.
* **This is not a backup**, and nothing in the cleanup page implies otherwise.
  It is a thing somebody chooses to make, and it is only as current as the
  moment they made it.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext as _

FORMAT_VERSION = 1

#: One HTML file a browser can still open. `assets/export.py`'s MAX_ROWS.
MAX_MESSAGES_PER_ROOM = 50_000
#: Request-shaped ceilings — `--no-caps` on the management command lifts these
#: two and nothing else, because they bound the work rather than the output.
MAX_MESSAGES_TOTAL = 200_000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
#: Derived from ROOMS, not from ireneo's member count: this archive's members
#: are rooms + attachments + 2, so a room ceiling is the honest one to state.
MAX_ROOMS = 2_000
MAX_ATTACHMENTS = 5_000
#: A single blob bigger than this is SKIPPED and named, never a reason to
#: refuse the whole export: ireneo's per-member limit guards *opening* an
#: untrusted bundle, and reusing it here would let one legacy 100 MB row make a
#: forum permanently un-exportable by any route.
MAX_BLOB_BYTES = 64 * 1024 * 1024

CHUNK = 1024 * 1024

#: Names Windows will not let you write, whatever the extension.
_DEVICE_NAMES = frozenset(
    ["aux", "con", "nul", "prn"]
    + [f"com{n}" for n in range(1, 10)]
    + [f"lpt{n}" for n in range(1, 10)])


class ExportTooLarge(Exception):
    """A cap was hit. Refused whole, never truncated.

    A truncated archive looks complete to whoever holds it, and they have no
    way to tell. So the refusal names the limit and the actual figure, and the
    operator decides what to do about it.
    """

    def __init__(self, what, limit, actual, unit=""):
        self.what, self.limit, self.actual, self.unit = what, limit, actual, unit
        super().__init__(
            _("%(what)s: this forum has %(actual)s%(unit)s and the limit is "
              "%(limit)s%(unit)s.") % {"what": what, "actual": actual,
                                       "limit": limit, "unit": unit})


# ---------------------------------------------------------------------------
# Names inside the archive
# ---------------------------------------------------------------------------

def attachment_member_name(sha256: str, filename: str) -> str:
    """``<sha256>-<filename>``, the filename reduced to what every filesystem
    accepts. Copied from `zenobia/limbo/ireneo/bundle.py` rather than imported:
    limbo is inert by construction and a wheel cannot import a host tree.

    Content-addressed, so the same picture posted in four rooms is one member.
    """
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[:120]
    return f"{sha256}-{safe or 'file'}"


def _unsafe(name: str) -> bool:
    """Also from ireneo's bundle. Nothing built here should be able to escape
    the archive root, and this is the assertion that says so."""
    return (name.startswith(("/", "\\")) or ".." in name.split("/")
            or "\\" in name or ":" in name[:2])


def room_member_name(slug: str, seen: set) -> str:
    """One file per room, unique case-INSENSITIVELY.

    `Alpha` and `alpha` are different slugs to Postgres and the same file on
    macOS and Windows, which would silently overwrite one room with the other
    the moment somebody unzipped it.
    """
    base = slug or "room"
    if base.split(".")[0].lower() in _DEVICE_NAMES:
        base = f"{base}-room"
    candidate, n = base, 1
    while candidate.casefold() in seen:
        n += 1
        candidate = f"{base}-{n}"
    seen.add(candidate.casefold())
    return f"rooms/{candidate}.html"


def export_filename(when=None, *, channel=None) -> str:
    stamp = (when or timezone.now()).strftime("%Y%m%d-%H%M%S")
    if channel is not None:
        # The room's slug is already filename-safe (SlugField), and naming the
        # room is the point: an operator with a folder of these needs to know
        # which is which without opening them.
        return f"forum-{channel.slug}-{stamp}.zip"
    return f"forum-export-{stamp}.zip"


# ---------------------------------------------------------------------------
# The survey
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AttachmentPlan:
    message_id: str
    member: str
    storage_name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class RoomPlan:
    channel_id: int
    name: str
    slug: str
    member: str
    messages: int
    first: object
    last: object


@dataclass
class Plan:
    rooms: list = field(default_factory=list)
    attachments: dict = field(default_factory=dict)   # member -> AttachmentPlan
    by_message: dict = field(default_factory=dict)    # message id -> member
    skipped: list = field(default_factory=list)
    total_messages: int = 0
    total_bytes: int = 0
    generated_at: object = None
    actor: str = ""
    #: The slug of the ONE room this plan covers, or "" for the whole forum.
    #: Recorded at survey time so the index page and the manifest can say what
    #: the file IS — a per-room archive is handed to that room's members, and
    #: one that declares itself a whole-forum staff export overstates both what
    #: it holds and who must have made it.
    channel_slug: str = ""


def _storage():
    from .models import ForumMessage

    return ForumMessage._meta.get_field("attachment").storage


def survey(*, actor="", channel=None) -> Plan:
    """Measure everything and decide whether to refuse. Writes nothing.

    With `channel`, the archive is ONE ROOM — which is the difference between
    an operator's snapshot of the platform and a room's own record of itself.
    The narrow form is what the room's Settings tab offers, and it is the one
    that can be handed to the people in that room: it contains their
    conversation and nothing from any room they are not in.

    THE SCOPING IS HERE AND NOWHERE ELSE. Every later pass — `_days`,
    `render_room`, `render_index`, `manifest`, `stream_archive` — walks
    `plan.rooms`, so narrowing this list is what makes a per-room export
    leak-proof. A filter applied in the renderer instead would still have
    measured, and named in the manifest, rooms the reader may not see.
    """
    from .models import ForumChannel, ForumMessage

    plan = Plan(generated_at=timezone.now(), actor=actor,
                channel_slug=channel.slug if channel is not None else "")
    channels = ([channel] if channel is not None
                else list(ForumChannel.objects.all().order_by("slug", "pk")))
    if len(channels) > MAX_ROOMS:
        raise ExportTooLarge(_("Rooms"), MAX_ROOMS, len(channels))

    storage = _storage()
    seen_files: set = set()

    for channel in channels:
        rows = channel.messages.all().order_by("created_at", "id")
        count = rows.count()
        if count > MAX_MESSAGES_PER_ROOM:
            raise ExportTooLarge(
                _("Messages in “%(room)s”") % {"room": channel.name},
                MAX_MESSAGES_PER_ROOM, count)
        plan.total_messages += count
        plan.rooms.append(RoomPlan(
            channel_id=channel.pk, name=channel.name, slug=channel.slug,
            member=room_member_name(channel.slug, seen_files),
            messages=count,
            first=rows.values_list("created_at", flat=True).first(),
            last=rows.values_list("created_at", flat=True).last(),
        ))

        # Deleted messages contribute no file: a tombstone carries metadata
        # only, so its blob is neither written nor referenced.
        with_files = (rows.filter(deleted_at__isnull=True)
                      .exclude(attachment="").exclude(attachment__isnull=True))
        for message in with_files.only("id", "attachment", "attachment_name",
                                       "attachment_sealed"):
            name = message.attachment.name
            if message.attachment_sealed:
                # A sealed blob is ciphertext on disk; the archive carries
                # plaintext pages, so the file is named as left out rather
                # than copied in a form nobody can open.
                plan.skipped.append({"message": str(message.id),
                                     "file": message.attachment_name,
                                     "why": "encrypted"})
                continue
            try:
                if not storage.exists(name):
                    plan.skipped.append({"message": str(message.id),
                                         "file": message.attachment_name,
                                         "why": "missing"})
                    continue
                size = storage.size(name)
                if size > MAX_BLOB_BYTES:
                    plan.skipped.append({"message": str(message.id),
                                         "file": message.attachment_name,
                                         "why": "too-large", "bytes": size})
                    continue
                digest = hashlib.sha256()
                with storage.open(name, "rb") as fh:
                    for block in iter(lambda: fh.read(CHUNK), b""):
                        digest.update(block)
            except Exception as exc:  # noqa: BLE001 — name it, do not abort
                plan.skipped.append({"message": str(message.id),
                                     "file": message.attachment_name,
                                     "why": f"unreadable: {exc}"[:200]})
                continue

            member = "attachments/" + attachment_member_name(
                digest.hexdigest(), message.attachment_name or "file")
            plan.by_message[str(message.id)] = member
            if member not in plan.attachments:
                plan.attachments[member] = AttachmentPlan(
                    message_id=str(message.id), member=member,
                    storage_name=name, size=size, sha256=digest.hexdigest())
                plan.total_bytes += size

    if plan.total_messages > MAX_MESSAGES_TOTAL:
        raise ExportTooLarge(_("Messages"), MAX_MESSAGES_TOTAL,
                             plan.total_messages)
    if len(plan.attachments) > MAX_ATTACHMENTS:
        raise ExportTooLarge(_("Files"), MAX_ATTACHMENTS,
                             len(plan.attachments))
    if plan.total_bytes > MAX_TOTAL_BYTES:
        raise ExportTooLarge(_("Total size"), MAX_TOTAL_BYTES // (1024 * 1024),
                             plan.total_bytes // (1024 * 1024), unit=" MB")
    return plan


def lift_caps():
    """What `--no-caps` lifts, and what it never lifts.

    The two request-shaped ceilings go; the per-room one stays, because it is
    about what a browser can open rather than about what this process can do.
    """
    global MAX_MESSAGES_TOTAL, MAX_TOTAL_BYTES
    MAX_MESSAGES_TOTAL = 10 ** 12
    MAX_TOTAL_BYTES = 10 ** 15


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _days(rows, plan, key=None):
    """Messages folded into local-date sections, in order.

    Folded in Python rather than with TruncDate: the day boundary has to be the
    reader's, and this keeps the query count independent of how much was said.
    """
    out, current, bucket = [], None, []
    for row in rows:
        day = timezone.localtime(row.created_at).date()
        if day != current:
            if current is not None:
                out.append((current, bucket))
            current, bucket = day, []
        bucket.append(_message_context(row, plan, key))
    if current is not None:
        out.append((current, bucket))
    return out


def _message_context(row, plan, key=None):
    from .store import body_of

    member = plan.by_message.get(str(row.id), "")
    return {
        "id": str(row.id),
        "deleted": bool(row.deleted_at),
        "sender": row.sender_name or _("Unknown member"),
        "created_at": timezone.localtime(row.created_at),
        "edited": bool(row.edited_at),
        # An encrypted room's archive is decrypted with the room key: the
        # archive IS the plaintext copy somebody chose to take (SECURITY.md).
        "body": body_of(row, key),
        "reply_to": str(row.reply_to_id) if row.reply_to_id else "",
        "kind": row.msg_type,
        # `../attachments/…` because every room page lives one level down.
        "attachment": ("../" + member) if member else "",
        "attachment_name": row.attachment_name if member else "",
        "attachment_size": row.attachment_size if member else None,
    }


def render_room(room_plan, plan) -> str:
    from .models import ForumMessage

    from .models import ForumChannel
    from .rooms import RoomKeyUnavailable, open_key

    rows = list(ForumMessage.objects.filter(channel_id=room_plan.channel_id)
                .order_by("created_at", "id"))
    here = {str(r.id) for r in rows}
    key = None
    channel = ForumChannel.objects.filter(pk=room_plan.channel_id).first()
    if channel is not None and channel.is_encrypted:
        try:
            key = open_key(channel)
        except RoomKeyUnavailable:
            key = None
    days = _days(rows, plan, key=key)
    for _day, bucket in days:
        for message in bucket:
            # A reply link only when its target is in THIS file; nothing links
            # across room pages, and a dangling anchor is worse than a
            # sentence saying where it went.
            if message["reply_to"] and message["reply_to"] not in here:
                message["reply_to"] = ""
                message["reply_elsewhere"] = True
    return render_to_string("forum/export/room.html", {
        "room": room_plan, "days": days,
        "generated_at": plan.generated_at, "actor": plan.actor,
    })


def render_index(plan) -> str:
    return render_to_string("forum/export/index.html", {
        "rooms": plan.rooms,
        "generated_at": plan.generated_at,
        "actor": plan.actor,
        "attachments": len(plan.attachments),
        "total_bytes": plan.total_bytes,
        "total_messages": plan.total_messages,
        "skipped": plan.skipped,
        "channel_slug": plan.channel_slug,
    })


def manifest(plan, *, written, language) -> str:
    return json.dumps({
        "format": FORMAT_VERSION,
        "generated_at": plan.generated_at.isoformat(),
        "exported_by": plan.actor,
        "language": language,
        # The one machine-readable field that says what this file is. The
        # per-room value matters MORE than the wide one: that archive is the
        # one handed to a room's members, and for a day it declared itself an
        # operator export of every room, private ones included.
        "scope": (f"single-room-export:{plan.channel_slug}"
                  if plan.channel_slug else "all-rooms-operator-export"),
        "rooms": [{"name": r.name, "slug": r.slug, "file": r.member,
                   "messages": r.messages} for r in plan.rooms],
        "messages": plan.total_messages,
        "attachments_written": written,
        "attachments_planned": len(plan.attachments),
        "bytes": plan.total_bytes,
        "skipped": plan.skipped,
        "caps": {"messages_per_room": MAX_MESSAGES_PER_ROOM,
                 "messages_total": MAX_MESSAGES_TOTAL,
                 "rooms": MAX_ROOMS, "attachments": MAX_ATTACHMENTS,
                 "total_bytes": MAX_TOTAL_BYTES,
                 "blob_bytes": MAX_BLOB_BYTES},
    }, indent=2, sort_keys=True, ensure_ascii=False)


class _Sink:
    """Write-only, so `ZipFile` never tries to seek backwards.

    Deliberately without `seekable`, `tell` or `seek`: `ZipFile` probes for
    them and falls back to its append-only path on AttributeError. Adding a
    `seekable()` that answers True silently re-enables the seek-back path and
    corrupts a streamed archive.
    """

    def __init__(self):
        self.buffer = io.BytesIO()

    def write(self, data):
        self.buffer.write(data)
        return len(data)

    def flush(self):
        pass

    def take(self) -> bytes:
        chunk = self.buffer.getvalue()
        self.buffer = io.BytesIO()
        return chunk


def _info(name, *, compressed=True):
    """A fixed timestamp and mode, so two exports of unchanged data match."""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = (zipfile.ZIP_DEFLATED if compressed
                          else zipfile.ZIP_STORED)
    info.external_attr = 0o644 << 16
    return info


def stream_archive(plan):
    """Yield the ZIP, member by member. A plain generator, so the management
    command and every test use exactly the code the view streams."""
    language = settings.LANGUAGE_CODE
    sink = _Sink()
    storage = _storage()
    written = 0

    with translation.override(language):
        with zipfile.ZipFile(sink, "w") as archive:
            archive.writestr(_info("index.html"), render_index(plan))
            chunk = sink.take()
            if chunk:
                yield chunk

            for room in plan.rooms:
                assert not _unsafe(room.member), room.member
                archive.writestr(_info(room.member), render_room(room, plan))
                chunk = sink.take()
                if chunk:
                    yield chunk

            for member in sorted(plan.attachments):
                item = plan.attachments[member]
                assert not _unsafe(member), member
                # Re-checked immediately before reading: the forum is live and
                # cleanup deletes exactly these blobs. A vanished file would
                # otherwise raise after the 200 has begun, and a SHRUNK one
                # would produce no error at all — a short member inside a
                # structurally valid archive.
                try:
                    if not storage.exists(item.storage_name) or \
                            storage.size(item.storage_name) != item.size:
                        plan.skipped.append({"message": item.message_id,
                                             "file": member,
                                             "why": "changed-during-export"})
                        continue
                    with storage.open(item.storage_name, "rb") as source, \
                            archive.open(_info(member, compressed=False),
                                         "w") as target:
                        for block in iter(lambda: source.read(CHUNK), b""):
                            target.write(block)
                            piece = sink.take()
                            if piece:
                                yield piece
                except Exception as exc:  # noqa: BLE001
                    plan.skipped.append({"message": item.message_id,
                                         "file": member,
                                         "why": f"unreadable: {exc}"[:200]})
                    continue
                written += 1
                chunk = sink.take()
                if chunk:
                    yield chunk

            # Last, so it can report what actually happened rather than what
            # the survey expected to happen.
            archive.writestr(_info("manifest.json"),
                             manifest(plan, written=written, language=language))

    chunk = sink.take()
    if chunk:
        yield chunk
