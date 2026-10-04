"""Folders that update as they change (2026-10-04).

A file list that has a folder open names it to the long-poll door
(``notify:api_wait``, ``?folders=<ids>``); the door answers when something
changed in it, with the ids of the files the reader is shown there, and the
page fetches the rows that are new to it from ``vault:file_row`` and drops
the ones that left. This module is the vault's half of that:

* ``publish`` — on ``signals.file_changed`` — says that a FOLDER changed
  (``toto.core.live``, key ``folder.<directory pk>``): nothing about which
  file, what it is called or who did it. An upload, a restore, a rename, a
  new body or a lock touch the file's folder; a trashing or a delete the
  folder it left; a move both. A file at a bucket's top level is in no
  folder, and nothing is said.
* ``watched`` — which of these folders may this account watch? The rule of
  the page that lists them, asked at EVERY poll: the bucket's clearances let
  them read (pessimistic, no owner bypass, ``access.gate_by_bucket``), and
  the folder's own access list admits them (as
  ``VaultDirectory.user_can_access``). A folder that is not there and one
  that is not theirs are left out the same way, and the door never says
  which.
* ``rows`` — what a changed folder holds FOR THIS READER: the ids of the
  files the list would show them there (``views.listed_files``, the same
  queryset as the page), each with a version tag. Watching a folder is not
  reading every file in it — a folder open to everybody holds private files
  — so a file the reader is not shown is not in their answer, and neither
  is the fact that it exists. The page compares: an id it does not have, or
  has under another tag, is fetched from the row door (which asks
  ``access.may_read`` again); a row it has whose id is gone is dropped.
* ``row_version`` — the tag: a keyed hash of the columns a listing shows
  (``models.LIVE_FIELDS``). It changes when the row does, and says nothing
  of what the row holds.

Connected only where ``toto.notify`` — the app that serves the door — is
installed (``VaultConfig.ready``); elsewhere nothing is published.
"""

from __future__ import annotations

import hashlib

from . import access

#: A folder with more files than this is not listed for a page to compare:
#: it is told that the folder changed and draws it again at the next load.
MAX_ROWS = 2000

_version_key = None


def folder_key(directory_id) -> str:
    """One folder, as ``toto.core.live`` names it."""
    return f"folder.{int(directory_id)}"


def watched(user, directory_ids) -> list:
    """The ids among ``directory_ids`` that ``user`` may watch, in the order
    given. See the module docstring."""
    from .models import VaultDirectory

    if not getattr(user, "is_authenticated", False):
        return []
    asked = []
    for raw in directory_ids:
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            continue
        if pk > 0 and pk not in asked:
            asked.append(pk)
    if not asked:
        return []
    readable = set(access.gate_by_bucket(user, VaultDirectory.objects.filter(pk__in=asked))
                   .values_list("pk", flat=True))
    if not readable:
        return []
    if not getattr(user, "is_superuser", False):
        # The folder's own access list, for all of them in one query: a
        # folder with no list is open, one with a list admits its names.
        listed = {}
        through = VaultDirectory.allowed_users.through
        for directory_id, user_id in through.objects.filter(
                vaultdirectory_id__in=readable).values_list("vaultdirectory_id", "user_id"):
            listed.setdefault(directory_id, set()).add(user_id)
        readable = {pk for pk in readable if pk not in listed or user.pk in listed[pk]}
    return [pk for pk in asked if pk in readable]


def may_watch(user, directory_id) -> bool:
    """``watched`` for one folder."""
    return bool(watched(user, [directory_id]))


def _version(state: dict) -> str:
    from .models import LIVE_FIELDS

    global _version_key
    if _version_key is None:
        from django.conf import settings

        _version_key = hashlib.sha256(
            ("toto.vault.live.row:" + settings.SECRET_KEY).encode("utf-8")).digest()
    text = "\x1f".join(f"{state.get(name)!s}" for name in LIVE_FIELDS)
    return hashlib.blake2s(text.encode("utf-8", "surrogatepass"), digest_size=5,
                           key=_version_key).hexdigest()


def row_version(vault_file) -> str:
    """The tag of one file's row as it stands. See the module docstring."""
    from .models import live_state

    return _version(live_state(vault_file))


def rows(user, directory_id):
    """``{file id: version tag}`` for the files the list shows ``user`` in
    this folder, or ``None`` for a folder too large to compare
    (``MAX_ROWS``). The caller has asked ``watched``."""
    from .models import LIVE_FIELDS
    from .views import listed_files

    found = list(listed_files(user).filter(directory_id=directory_id)
                 .values("pk", *LIVE_FIELDS)[:MAX_ROWS + 1])
    if len(found) > MAX_ROWS:
        return None
    return {str(row["pk"]): _version(row) for row in found}


def folders_of(kind, vault_file, was) -> list:
    """The folders one ``file_changed`` touches."""
    was = was or {}
    here = vault_file.__dict__.get("directory_id")
    before = was.get("directory_id")
    if kind in ("uploaded", "restored", "replaced", "changed"):
        found = [here]
    elif kind in ("trashed", "deleted"):
        found = [before]
    elif kind == "moved":
        found = [before, here]
    else:
        found = []
    return [pk for pk in dict.fromkeys(found) if pk]


def publish(sender=None, file=None, kind="", was=None, **kwargs) -> None:
    """``signals.file_changed``'s listener: say which folders changed."""
    from toto.core import live

    if file is None or not getattr(file, "pk", None):
        return
    for directory_id in folders_of(kind, file, was):
        live.publish(folder_key(directory_id))


def connect() -> None:
    from .signals import file_changed

    file_changed.connect(publish, dispatch_uid="toto.vault.live.publish")
