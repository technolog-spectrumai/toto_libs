"""What a notification says, by kind (2026-10-04).

A row keeps its kind and its parameters; the sentence is made here, when the
bell is drawn, in the reader's language. ``one`` is the sentence, ``many`` its
form for a burst folded into one row (``params["count"]`` above one) — a
lazy plural, so Polish gets its three forms.

Four kinds, all about a file in a bucket the recipient owns (``sources.py``):
uploaded, replaced, moved to the trash, restored. A sentence names only what
its recipient could already see when it was sent (``sources.py`` asks the
vault's own access rule), and never who did it: the actor is a key on the
row, shown beside the sentence and gone with the account. ``bucket_scoped``
kinds carry ``bucket_id``; the list drops such a row for a reader the bucket
is hidden from now (``services.listing``).

A kind this build does not know renders as nothing and is left out of the
list, so a row written by a newer build never breaks an older one's bell.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy


@dataclass(frozen=True)
class Kind:
    key: str
    icon: str
    one: object
    many: object = None
    bucket_scoped: bool = False


class _Params(dict):
    """A sentence missing a parameter says less; it never raises."""

    def __missing__(self, key):
        return ""


KINDS: dict[str, Kind] = {}


def register(kind: Kind) -> Kind:
    KINDS[kind.key] = kind
    return kind


def get(key: str):
    return KINDS.get(key)


def count_of(params) -> int:
    try:
        return max(1, int((params or {}).get("count") or 1))
    except (TypeError, ValueError):
        return 1


def text_of(kind_key: str, params) -> str:
    """The sentence for one row, in the active language; "" for a kind this
    build does not know."""
    kind = KINDS.get(kind_key)
    if kind is None:
        return ""
    values = _Params(params or {})
    count = count_of(values)
    values["count"] = count
    sentence = kind.many if (kind.many is not None and count > 1) else kind.one
    try:
        return str(sentence % values) if sentence is kind.many else str(sentence) % values
    except (KeyError, TypeError, ValueError):
        return str(kind.one)


# ---------------------------------------------------------------------------
# Files in a bucket of mine
# ---------------------------------------------------------------------------

VAULT_UPLOADED = register(Kind(
    "vault.uploaded", "fa-solid fa-upload",
    _("%(title)s was uploaded to %(bucket)s"),
    ngettext_lazy("%(count)s file was uploaded to %(bucket)s",
                  "%(count)s files were uploaded to %(bucket)s", "count"),
    bucket_scoped=True))
VAULT_REPLACED = register(Kind(
    "vault.replaced", "fa-solid fa-file-pen",
    _("%(title)s was replaced in %(bucket)s"),
    ngettext_lazy("%(count)s file was replaced in %(bucket)s",
                  "%(count)s files were replaced in %(bucket)s", "count"),
    bucket_scoped=True))
VAULT_TRASHED = register(Kind(
    "vault.trashed", "fa-solid fa-trash",
    _("%(title)s was moved to the trash in %(bucket)s"),
    ngettext_lazy("%(count)s file was moved to the trash in %(bucket)s",
                  "%(count)s files were moved to the trash in %(bucket)s", "count"),
    bucket_scoped=True))
VAULT_RESTORED = register(Kind(
    "vault.restored", "fa-solid fa-trash-arrow-up",
    _("%(title)s was restored from the trash in %(bucket)s"),
    ngettext_lazy("%(count)s file was restored from the trash in %(bucket)s",
                  "%(count)s files were restored from the trash in %(bucket)s", "count"),
    bucket_scoped=True))
