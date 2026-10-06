"""What erasing an account removes beyond the database's cascade (2026-10-01,
37c.21; called by ``erase_user`` — toto.socialhub's ``erasure`` module is the
request a member files, this is what the console then does).

``erase_user`` deletes the account and lets Django's collector decide the
rest. The privacy notice's survey found what that left behind, and the
notice promises it is gone:

* **the profile picture's file** — a row delete never touches the bytes of a
  file field;
* **the bodies of their files' saved versions** (``vault/versions/``) — the
  versions cascade with the files, the bodies have no link back
  (``vault.versions.drop_orphan_blobs``, as a purge does it);
* **on a host with the map (``toto.locations``, toto-geo): their home pin,
  and the addresses they added that nothing else uses** — the pin is a row of
  its own, so it stayed on the map, linked to nobody. A pin another person
  lives at, or a place is built on, stays (``toto.locations.erasure``). The
  address on the profile itself is text on the person's row since 2026-10-04
  and goes with it;
* **on a host with geography (``toto.geography``, toto-geo, 2026-10-06):
  their own point** — the ``Address`` row behind their ``PersonAddress``
  link, which belongs to nobody else (``toto.geography.erasure``);
* **their membership application and its references** — keyed by address,
  not by account (``socialhub.applications.of_member``); the audit chain
  keeps the admission;
* **their name and picture on the forum messages they sent**, and the
  pictures and voice recordings themselves (``toto.forum.erasure``);
* **their username in the name of their personal bucket and of their prepaid
  ledger account**, both kept for other people's sake
  (``vault.models.forget_personal_buckets``,
  ``toto.assets.prepaid.forget_holder``).

:func:`report` says what that will be, for ``plan``; :func:`gather` does the
renames and deletes that need the account still there and remembers the
rest; :func:`after_delete` removes the addresses; :func:`after_commit`
deletes the bytes once the erase is in — row first, bytes second.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.apps import apps

log = logging.getLogger("toto.core")


@dataclass
class Leftovers:
    """What :func:`gather` found and the later steps remove."""

    avatar: str = ""
    version_blobs: set = field(default_factory=set)
    home: int | None = None
    addresses: list = field(default_factory=list)
    #: Their own point's ``geography.Address`` ids (toto.geography).
    geography_addresses: list = field(default_factory=list)
    forum_blobs: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def _person(user):
    if not apps.is_installed("toto.people"):
        return None
    from toto.people.models import Person

    return Person.objects.filter(user=user).first()


def _version_blobs(user) -> set:
    """The version bodies only ``user``'s own files cite (their files cascade
    with the account; a body shared with another file's version stays)."""
    if not apps.is_installed("toto.vault"):
        return set()
    from toto.vault.models import FileVersion, VersionBlob

    theirs = set(FileVersion.objects.filter(file__owner=user).values_list("blob_id", flat=True))
    if not theirs:
        return set()
    shared = set(FileVersion.objects.filter(blob_id__in=theirs).exclude(file__owner=user)
                 .values_list("blob_id", flat=True))
    return set(VersionBlob.objects.filter(pk__in=theirs - shared).values_list("pk", flat=True))


def _count(value) -> int:
    try:
        return len(value)
    except TypeError:
        return value.count()


def _addresses(user, person) -> tuple[int | None, list[int]]:
    """On a host with the map: the home pin and the addresses they added
    that nothing else uses (``toto.locations.erasure``). Nothing elsewhere —
    the profile's address is text on the person's own row."""
    if not apps.is_installed("toto.locations"):
        return None, []
    from toto.locations.erasure import addresses_of

    return addresses_of(user, person)


def _geography_addresses(user, person) -> list[int]:
    """On a host with geography: the member's own point
    (``toto.geography.erasure``). A lazy import, as for the map above: no
    package-graph edge from toto-base."""
    if not apps.is_installed("toto.geography"):
        return []
    from toto.geography.erasure import addresses_of

    return addresses_of(user, person)


def report(user) -> tuple[dict, list[str]]:
    """``(beyond, notes)`` for ``plan``: counts and plain sentences. Writes
    nothing."""
    person = _person(user)
    beyond = {"avatar_file": int(bool(person is not None and person.avatar))}
    beyond["version_bodies"] = len(_version_blobs(user))
    home, own = _addresses(user, person)
    beyond["addresses"] = int(home is not None) + len(own)
    beyond["map_points"] = len(_geography_addresses(user, person))
    notes = []
    if apps.is_installed("toto.socialhub"):
        from toto.socialhub.applications import of_member
        from toto.socialhub.models import ReferenceRequest

        applications = of_member(user)
        beyond["applications"] = applications.count()
        beyond["references"] = ReferenceRequest.objects.filter(
            application__in=applications).count()
    if apps.is_installed("toto.forum"):
        from toto.forum.erasure import sent_by

        sent = sent_by(user)
        beyond["forum_messages"] = sent["messages"]
        beyond["forum_attachments"] = sent["attachments"]
        if sent["messages"]:
            notes.append(f"{sent['messages']} forum message(s) they sent keep their text, "
                         "signed “Former member”, without their name or picture; the "
                         f"{sent['attachments']} picture(s) and voice recording(s) among "
                         "them go, bytes and all.")
    if apps.is_installed("toto.vault"):
        from toto.vault.models import personal_buckets_of

        beyond["buckets_renamed"] = personal_buckets_of(user).count()
    if apps.is_installed("toto.assets"):
        from toto.assets.prepaid import prepaid_code
        from toto.assets.models import LedgerAccount

        beyond["ledger_accounts_renamed"] = LedgerAccount.objects.filter(
            code=prepaid_code(user.pk)).count()
    if beyond.get("buckets_renamed") or beyond.get("ledger_accounts_renamed"):
        notes.append("Their personal bucket and their prepaid ledger account stay, renamed "
                     "“… — deleted account”: neither keeps their username.")
    gone = [what for what, key in (("their profile picture's file", "avatar_file"),
                                   ("the bodies of their files' saved versions", "version_bodies"),
                                   ("their home pin and the addresses only they used",
                                    "addresses"),
                                   ("their point on the map", "map_points"),
                                   ("their membership application and its references",
                                    "applications"))
            if beyond.get(key)]
    if gone:
        notes.append("Also erased: " + "; ".join(gone) + ".")
    return beyond, notes


def gather(user) -> Leftovers:
    """Inside the erase's transaction, before the account goes: rename and
    delete what needs the account still there, and remember what goes later."""
    person = _person(user)
    left = Leftovers()
    if person is not None and person.avatar:
        left.avatar = person.avatar.name
    left.version_blobs = _version_blobs(user)
    left.home, left.addresses = _addresses(user, person)
    left.geography_addresses = _geography_addresses(user, person)
    if apps.is_installed("toto.socialhub"):
        from toto.socialhub.applications import of_member

        left.counts["applications"] = of_member(user).delete()[1].get(
            "socialhub.MembershipApplication", 0)
    if apps.is_installed("toto.forum"):
        from toto.forum.erasure import forget_sender

        left.forum_blobs = forget_sender(user)
    if apps.is_installed("toto.vault"):
        from toto.vault.models import forget_personal_buckets

        left.counts["buckets_renamed"] = forget_personal_buckets(user)
    if apps.is_installed("toto.assets"):
        from toto.assets.prepaid import forget_holder

        left.counts["ledger_accounts_renamed"] = forget_holder(user)
    return left


def after_delete(left: Leftovers) -> None:
    """Inside the transaction, once the account (and its person) is gone:
    the home pin and their unused addresses, and their own point."""
    if apps.is_installed("toto.geography") and left.geography_addresses:
        from toto.geography.erasure import delete_addresses as delete_points

        delete_points(left.geography_addresses)
    if not apps.is_installed("toto.locations"):
        return
    from toto.locations.erasure import delete_addresses

    delete_addresses(([left.home] if left.home else []) + list(left.addresses))


def after_commit(left: Leftovers) -> None:
    """Once the erase has committed: the bytes. A failure is logged — the
    rows are gone, and an orphan is better than a row pointing at nothing."""
    if left.avatar:
        from toto.people.models import Person

        try:
            Person._meta.get_field("avatar").storage.delete(left.avatar)
        except Exception:  # noqa: BLE001 - logged; the erase stands
            log.warning("erase: could not delete the avatar file %r", left.avatar,
                        exc_info=True)
    if left.version_blobs:
        from toto.vault.versions import drop_orphan_blobs

        try:
            drop_orphan_blobs(left.version_blobs)
        except Exception:  # noqa: BLE001
            log.warning("erase: could not drop version bodies %s", sorted(left.version_blobs),
                        exc_info=True)
    if left.forum_blobs:
        from toto.forum.erasure import delete_blobs

        delete_blobs(left.forum_blobs)
