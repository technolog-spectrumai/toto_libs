"""Reading and writing the notes people keep beside the ledger.

Every write in this module touches `LedgerTag`, `LedgerEntryTag` or
`LedgerEntryComment` and nothing else. `LedgerEntry` is read — to find out which
account a movement belongs to, which is what the permission check needs — and
never saved. `LedgerTransaction.description` and `.metadata`, the two fields the
hash chain covers, are not in any write path here at all.

There is one writer per operation on purpose. The entry reference is a bare
integer with no foreign key (see `LedgerEntryTag`), so the database will not
refuse a tag from one account attached to another account's movement — the
guard lives here, and the tests assert it. That is the trade the bare id buys,
and concentrating it in four functions is what makes it cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import (
    Asset,
    LedgerEntry,
    LedgerEntryComment,
    LedgerEntryTag,
    LedgerTag,
)

#: How many movements one page of the ledger shows.
PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------

def can_annotate_account(user, account) -> bool:
    """Whether this person may write notes against this account.

    Ownership or staff. A company account sets ``user=None`` by construction, so
    ownership can never be true there — the Business Center gates its own
    endpoints on board membership and calls the writers below directly, rather
    than this app learning what a company is.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return bool(account.user_id == user.pk or user.is_staff)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _entry_of(account, entry_id: int) -> LedgerEntry:
    """The movement, proved to belong to this account.

    The bare id means the database will not do this for us. Raising here rather
    than returning None so a caller cannot quietly write a decoration onto
    somebody else's movement by ignoring a falsy result.
    """
    entry = LedgerEntry.objects.filter(pk=entry_id, account=account).first()
    if entry is None:
        raise ValidationError("That movement is not on this account.")
    return entry


def tag_for(account, name: str) -> LedgerTag:
    """The account's tag by that name, created on first use.

    Case- and sigil-insensitive lookup: "#Rent", "rent" and " RENT " are all the
    same tag, because two tags that render identically would be a vocabulary
    nobody could use.
    """
    cleaned = (name or "").lstrip("#").strip()
    if not cleaned:
        raise ValidationError("A tag needs a name.")
    if len(cleaned) > 50:
        raise ValidationError("That tag name is too long.")

    existing = LedgerTag.objects.filter(account=account, name__iexact=cleaned).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return LedgerTag.objects.create(account=account, name=cleaned)
    except IntegrityError:
        # Raced, or collided on the slug of a differently-cased name. Either
        # way the vocabulary already has it.
        return LedgerTag.objects.get(account=account, name__iexact=cleaned)


def add_tag(account, entry_id: int, name: str, *, user=None) -> LedgerEntryTag:
    """Attach a tag to a movement. Idempotent."""
    _entry_of(account, entry_id)
    tag = tag_for(account, name)
    try:
        with transaction.atomic():
            return LedgerEntryTag.objects.create(
                entry_id=entry_id, tag=tag, created_by=user)
    except IntegrityError:
        # Already tagged. Saying so with an exception would make a double-click
        # an error, which it is not.
        return LedgerEntryTag.objects.get(entry_id=entry_id, tag=tag)


def remove_tag(account, entry_id: int, tag_pk: int) -> None:
    """Detach a tag. The vocabulary row survives, so filters and renames do."""
    _entry_of(account, entry_id)
    LedgerEntryTag.objects.filter(
        entry_id=entry_id, tag__account=account, tag_id=tag_pk).delete()


def set_comment(account, entry_id: int, body: str, *, user=None):
    """Write, rewrite or clear the note on a movement.

    An empty body deletes the row. Absence is the honest representation of "no
    note" — a stored empty string would make every un-annotated movement look
    annotated to any query counting them.
    """
    _entry_of(account, entry_id)
    body = (body or "").strip()
    if not body:
        LedgerEntryComment.objects.filter(entry_id=entry_id).delete()
        return None
    comment, _ = LedgerEntryComment.objects.update_or_create(
        entry_id=entry_id, defaults={"body": body, "author": user})
    return comment


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@dataclass
class LedgerRow:
    """One movement with everything a template needs already attached."""

    entry: LedgerEntry
    comment: LedgerEntryComment | None = None
    tags: list = field(default_factory=list)


def assets_moved(account) -> list[Asset]:
    """The currencies this ledger has actually moved, newest activity first.

    Sourced from entries rather than holdings on purpose: "what has moved
    through here" is the ledger's own question, and it cannot go stale if
    holdings handling ever changes.

    Asked from the Asset side in one query. Going the other way —
    ``LedgerEntry...values_list("asset_id").distinct()`` — silently does not
    work: ``LedgerEntry.Meta.ordering`` puts ``created_at`` into the SELECT, so
    the DISTINCT is over ``(asset_id, created_at)`` and returns one row per
    movement rather than one per currency.
    """
    return list(Asset.objects.filter(entries__account=account)
                .distinct().order_by("unit_name"))


def ledger_page_context(account, *, asset=None, tag_slug: str = "",
                        page: int = 1, per_page: int = PAGE_SIZE,
                        can_annotate: bool = False) -> dict:
    """Everything one page of a decorated ledger needs, in a fixed 5 queries.

    Flat in page size: the decorations are fetched for the page's ids in two
    queries and stitched in Python. There is no reverse accessor from an entry
    to its decorations — the bare id makes an N+1 unreachable rather than merely
    discouraged.
    """
    from django.core.paginator import Paginator

    entries = (LedgerEntry.objects.filter(account=account)
               .select_related("transaction", "asset")
               # NOT the model's Meta.ordering, which is ASCENDING — the newest
               # movements would land on the last page. `created_at` is
               # auto_now_add and both legs of a transfer share a tick, so `-pk`
               # is what makes the order total and the pages stable.
               .order_by("-created_at", "-pk"))
    if asset is not None:
        entries = entries.filter(asset=asset)
    if tag_slug:
        entries = entries.filter(pk__in=LedgerEntryTag.objects.filter(
            tag__account=account, tag__slug=tag_slug).values("entry_id"))

    page_obj = Paginator(entries, per_page).get_page(page)
    ids = [entry.pk for entry in page_obj]

    comments = {c.entry_id: c for c in LedgerEntryComment.objects
                .filter(entry_id__in=ids).select_related("author")}
    tags: dict[int, list] = {}
    for link in (LedgerEntryTag.objects.filter(entry_id__in=ids)
                 .select_related("tag").order_by("tag__name")):
        tags.setdefault(link.entry_id, []).append(link)

    return {
        "account": account,
        "page_obj": page_obj,
        "rows": [LedgerRow(entry=entry,
                           comment=comments.get(entry.pk),
                           tags=tags.get(entry.pk, []))
                 for entry in page_obj],
        "ledger_assets": assets_moved(account),
        "ledger_vocabulary": list(LedgerTag.objects.filter(account=account)),
        "asset_filter": asset,
        "tag_filter": tag_slug,
        "can_annotate": can_annotate,
    }
