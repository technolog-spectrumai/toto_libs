"""What a notification says, by kind (2026-10-04).

A row keeps its kind and its parameters; the sentence is made here, when the
bell is drawn, in the reader's language. ``one`` is the sentence, ``many`` its
form for a burst folded into one row (``params["count"]`` above one) — a
lazy plural, so Polish gets its three forms.

A sentence names only what its recipient could already see when it was sent
(``sources.py`` asks the owning app's own access rule), and never who did it:
the actor is a key on the row, shown beside the sentence and gone with the
account. ``bucket_scoped`` kinds carry ``bucket_id``; the list drops such a
row for a reader the bucket is hidden from now (``services.listing``).

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
# Files in my buckets
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

# ---------------------------------------------------------------------------
# Shares and clearances
# ---------------------------------------------------------------------------

FOLDER_SHARED = register(Kind(
    "vault.folder_shared", "fa-solid fa-folder-open",
    _("You were given access to the folder %(folder)s in %(bucket)s"),
    bucket_scoped=True))
BUCKET_GIVEN = register(Kind(
    "vault.bucket_given", "fa-solid fa-database",
    _("The bucket %(bucket)s was set up for you"),
    bucket_scoped=True))
CLEARANCE_GRANTED = register(Kind(
    "clearance.granted", "fa-solid fa-key",
    _("You were given the clearance %(clearance)s")))
CLEARANCE_REMOVED = register(Kind(
    "clearance.removed", "fa-solid fa-key",
    _("The clearance %(clearance)s was taken from you")))

# ---------------------------------------------------------------------------
# Transfers and background jobs
# ---------------------------------------------------------------------------

TRANSFER_DONE = register(Kind(
    "job.transfer_done", "fa-solid fa-right-left",
    _("Your transfer to %(bucket)s finished")))
TRANSFER_FAILED = register(Kind(
    "job.transfer_failed", "fa-solid fa-triangle-exclamation",
    _("Your transfer to %(bucket)s failed")))
ZIP_DONE = register(Kind(
    "job.zip_done", "fa-solid fa-file-zipper",
    _("Your archive is ready")))
ZIP_FAILED = register(Kind(
    "job.zip_failed", "fa-solid fa-triangle-exclamation",
    _("Your archive could not be made")))
REFRESH_DONE = register(Kind(
    "job.refresh_done", "fa-solid fa-rotate",
    _("The refresh of %(bucket)s finished")))
REFRESH_FAILED = register(Kind(
    "job.refresh_failed", "fa-solid fa-triangle-exclamation",
    _("The refresh of %(bucket)s failed")))

# ---------------------------------------------------------------------------
# Account and security
# ---------------------------------------------------------------------------

NEW_SIGN_IN = register(Kind(
    "account.new_sign_in", "fa-solid fa-right-to-bracket",
    _("A new sign-in to your account")))
PASSWORD_CHANGED = register(Kind(
    "account.password_changed", "fa-solid fa-lock",
    _("Your password was changed")))
EMAIL_CHANGED = register(Kind(
    "account.email_changed", "fa-solid fa-envelope",
    _("Your e-mail address was changed")))
EXPORT_READY = register(Kind(
    "privacy.export_ready", "fa-solid fa-box-archive",
    _("The copy of your data is ready")))
EXPORT_FAILED = register(Kind(
    "privacy.export_failed", "fa-solid fa-triangle-exclamation",
    _("The copy of your data could not be made")))
ERASURE_DECLINED = register(Kind(
    "privacy.erasure_declined", "fa-solid fa-user-xmark",
    _("Your request to erase your account was declined")))

#: The mailed notices (``toto.core.notices``) that are said in the bell too.
#: Not ``email_change_confirm`` — a link to act on, mailed to an address the
#: account does not have yet — and not the operators' alerts.
NOTICE_KINDS = {
    "new_sign_in": NEW_SIGN_IN.key,
    "password_changed": PASSWORD_CHANGED.key,
    "email_changed": EMAIL_CHANGED.key,
}
