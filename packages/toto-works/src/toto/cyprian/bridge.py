"""Documents that belong to something else.

A cyprian document is one vault file and nothing more, so "who may edit this"
has always been ``VaultFile.owner == request.user``. That is the right answer
for a personal document and the wrong one for a document that is really a view
onto another app's object — a contract's prose, a project wiki page. Those have
an owner in the vault, because somebody has to hold the bytes, and a DIFFERENT
set of editors, decided by the app that owns the thing.

A **bridge** is that app's answer. Cyprian never imports the owning app:
discovery is ``autodiscover_plugins`` from ``CyprianConfig.ready()``, so a
bridge belonging to an app that is not installed is simply never imported —
which is what lets ``toto.kanban`` ship one in this same wheel to hosts that
have no writer at all.

Authorisation
-------------
``claim(vault_file)`` is the trust anchor, and it must be answered from data the
REQUESTER CANNOT WRITE. That is the whole security property here, and it is easy
to get wrong in two specific ways:

* ``document.meta`` is written by ``document_save``, so anyone who can edit any
  document can put any key in it. A bridge that authorised on meta would let a
  user hand their own file to a project by claiming it belongs to one.
* A ``vault_file`` foreign key on the owning app's row is only as trustworthy as
  the form that sets it. If a project member can point a page at an arbitrary
  ``VaultFile`` pk, the bridge will faithfully authorise them onto somebody
  else's private file.

So the rule for an owning app is: **the column your ``claim`` reads must be
written by ``open_document`` and by nothing else** — no ModelForm field, no
admin field, no API. ``open_document`` mints the file itself and never accepts a
pk from the caller, exactly as the contract bridge always did.

``meta[key]`` is still stamped, because it round-trips through the format for
free and so survives a download, a hand edit and a restore from backup. It is a
breadcrumb for putting a stray file back, never a permission. When the meta and
the column disagree, the column wins, because only one of them is ours.
"""

from __future__ import annotations

import hashlib
import logging
from typing import ClassVar

from django.core.files.base import ContentFile
from django.utils.text import slugify

from toto.core.plugin import BasePlugin
from toto.vault.models import VaultFile

logger = logging.getLogger(__name__)


def read_raw(vault_file: VaultFile) -> str:
    """Raw UTF-8 text via a fresh storage handle."""
    with vault_file.file.storage.open(vault_file.file.name, "rb") as fh:
        return fh.read().decode("utf-8")


def unique_key(base: str, bucket) -> str:
    key, n = base or "document", 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        n += 1
        key = f"{base}-{n}"
    return key


class DocumentBridge(BasePlugin):
    """One owning app's claim over the documents that belong to it.

    ``key`` is also the ``document.meta`` key the link is stamped under, so that
    a file carries the name of whatever owns it. Registration goes through
    ``BasePlugin``, which raises on a duplicate key — the same guard, and the
    same reason, as ``VaultEditorPlugin``.
    """

    registry: ClassVar[dict[str, "DocumentBridge"]] = {}

    # -- resolution -----------------------------------------------------------

    @classmethod
    def for_file(cls, vault_file, document=None) -> "tuple[DocumentBridge, object] | None":
        """The bridge that owns this file, and the object it owns it for.

        Asks every registered bridge, in ``order``, and takes the first claim.
        Order is load-bearing: a bridge that answers from its own column outranks
        one that can only read the document, so put the trustworthy ones first.

        ``document`` is the already-parsed document, passed in so a bridge that
        does need the meta does not re-read and re-parse the file behind the
        caller's back. It is an optimisation for the weak case, never a
        permission — see the module docstring.
        """
        for bridge in cls.all():
            owner_object = bridge.claim(vault_file, document)
            if owner_object is not None:
                return bridge, owner_object
        return None

    def claim(self, vault_file, document=None):
        """The object this file is the prose of, or None.

        SHOULD be answered from a column the owning app controls — see the
        module docstring. Returning None means "not mine", not "denied".
        """
        return None

    # -- the questions cyprian asks -------------------------------------------

    def can_edit(self, user, owner_object) -> bool:
        """Closed by default: a bridge that answers nothing grants nothing."""
        return False

    def can_read(self, user, owner_object) -> bool:
        return self.can_edit(user, owner_object)

    def write_back(self, document, owner_object, *, user) -> None:
        """Put the writer's HTML back into the object it came from."""

    def return_url(self, owner_object) -> str:
        """Where the writer's back arrow goes. Blank falls back to the library."""
        return ""

    def return_label(self, owner_object) -> str:
        return ""


def may_edit(user, vault_file, document=None) -> bool:
    """May this user write this document file? Owner, or a bridge says so.

    The single source of the answer. ``views._open_document`` asks it to decide
    whether to hand back the writer, and ``plugins.vault_access_plugins`` asks
    it on the vault's behalf so the lock and version endpoints reach the same
    verdict. They used to disagree — the vault's endpoints never consulted a
    bridge at all — which is precisely the drift this function exists to stop.

    Deliberately free of side effects (no ``_adopt``): the caller that owns the
    file does that in its own branch, because looking at a file must not retype
    somebody else's row.

    ``document`` is the already-parsed document when the caller has one, purely
    so a bridge that reads meta need not re-read the file. Never a permission.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if vault_file.owner_id == user.pk:
        return True
    if vault_file.is_encrypted:
        # No readable meta, so no bridge can claim it: owner only, and the
        # owner branch above already answered.
        return False

    match = DocumentBridge.for_file(vault_file, document)
    return match is not None and match[0].can_edit(user, match[1])


def open_document(*, key: str, ref: str, title: str, seed_html: str, owner,
                  bucket=None, directory=None, toc: bool = False,
                  document_title: str = "") -> VaultFile:
    """Mint the document behind an owning app's object — once, then reuse it.

    Generalised from ``from_contract``, which did exactly this for one key:
    create a companion document seeded from the owner's own HTML, stamp the link
    into ``meta[key]``, and hand back the file to redirect to.

    The seeding happens ONCE, and the caller is responsible for storing the
    returned file on its own row. After that the file IS the text; re-seeding on
    every open would silently revert the writer's work.

    Note what this function does NOT take: a ``VaultFile`` pk. The file is
    created here or it is not created at all, which is what makes the owning
    app's column safe to authorise against.
    """
    from . import document_format
    from toto.antivirus.sanitize import sanitize_content

    if bucket is None:
        from toto.vault.views import resolve_new_file_target
        bucket, directory = resolve_new_file_target(owner, None, None)

    # A same-named document already sitting in the target spot is adopted rather
    # than duplicated: that is how a page whose row lost its pointer — the owner
    # was deleted, a restore dropped it — finds its prose again instead of
    # silently starting blank beside it.
    found = VaultFile.objects.filter(
        bucket=bucket, directory=directory, title=title,
        file_type="document").first()
    if found is not None:
        return found

    document = document_format.new_document(document_title or "")
    body = sanitize_content(seed_html or "")
    if body:
        document.content = body
    document.meta[key] = str(ref)
    # No contents page by default: an owning app's object has its own front
    # matter, and a wiki page is read top to bottom.
    document.toc = toc

    xml = document_format.dumps(document).encode("utf-8")
    vault_file = VaultFile(
        owner=owner, title=title,
        key=unique_key(slugify(title.rsplit(".", 1)[0]) or "document", bucket),
        file_type="document", bucket=bucket, directory=directory,
        is_public=False)
    vault_file.save()
    vault_file.file.save(title, ContentFile(xml), save=True)
    vault_file.content_hash = hashlib.sha256(xml).hexdigest()
    vault_file.file_size_bytes = len(xml)
    vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    return vault_file


def write_back(vault_file, document, *, user) -> None:
    """Hand a saved document back to whatever owns it.

    Called on every save, so the owning app's own surfaces render what was just
    written — that is what makes cyprian the EDITOR rather than a copy of the
    text.

    Resolved from the FILE, not from the document's meta: the save that triggers
    this is the same request that could have rewritten the meta, so trusting it
    here would let a writer redirect their prose into somebody else's object.

    Silent on a stale link, a refused permission or a bridge that raises. A
    document that outlived its object is still a document, and turning a
    successful save into a 500 would lose the writer's work over someone else's
    bug. Logged, because silence is a debugging problem, not a design one.
    """
    match = DocumentBridge.for_file(vault_file, document)
    if match is None:
        return
    bridge, owner_object = match
    try:
        if not bridge.can_edit(user, owner_object):
            return
        bridge.write_back(document, owner_object, user=user)
    except Exception:                                  # noqa: BLE001
        logger.exception(
            "bridge %s failed to write back for %r", bridge.get_key(), owner_object)
