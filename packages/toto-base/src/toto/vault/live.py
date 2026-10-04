"""Folders that update as they change (2026-10-04).

A file list that has a folder open asks the live socket to WATCH it
(``toto.notify.consumers.LiveConsumer``); from then on the page is told when
a file in that folder is added, changed or removed, fetches that one row from
``vault:file_row`` and redraws it. This module is the vault's half of that:

* ``publish`` — on ``signals.file_changed`` — sends a small event to the
  group ``folder.<directory pk>``: a kind, the file's id, the folder's id.
  **No name.** Upload and restore are ``added``; trash and delete are
  ``removed``; a move is ``removed`` from the old folder and ``added`` to
  the new one; a rename, a new body, a lock are ``changed``. A file at a
  bucket's top level is in no folder, and nobody is told.
* ``may_watch`` — may this account watch that folder at all? The rule of the
  page that lists it: the bucket's clearances let them read (pessimistic, no
  owner bypass, ``access.gate_by_bucket``), and the folder's own access list
  admits them (``VaultDirectory.user_can_access``). A folder that is not
  there and one that is not theirs answer the same.
* ``may_see`` — asked for EVERY event, for every socket it is about to reach:
  ``access.may_read`` on the file. Watching a folder is not reading every
  file in it — a folder open to everybody holds private files — so an event
  about a file the reader may not read is dropped before it leaves the
  server, and with it the fact that the file exists. A clearance taken away
  after the watch began stops the events the same way. For a file that has
  left the folder the question is asked of the row as it stood there (the
  event remembers its owner, its public flag and its bucket; they never
  reach a browser).

Connected only where ``toto.notify`` — the app that serves the socket — is
installed (``VaultConfig.ready``); elsewhere nothing is published.
"""

from __future__ import annotations

from . import access

ADDED = "added"
CHANGED = "changed"
REMOVED = "removed"


def folder_group(directory_id) -> str:
    """Every socket watching one folder."""
    return f"folder.{int(directory_id)}"


def may_watch(user, directory_id) -> bool:
    """See the module docstring."""
    from .models import VaultDirectory

    if not getattr(user, "is_authenticated", False):
        return False
    try:
        directory_id = int(directory_id)
    except (TypeError, ValueError):
        return False
    directory = access.gate_by_bucket(
        user, VaultDirectory.objects.filter(pk=directory_id)).first()
    return directory is not None and directory.user_can_access(user)


def may_see(user, event: dict) -> bool:
    """May ``user`` be told this folder event? ``access.may_read`` on the
    file — the row itself, or for a ``removed`` one the row as it stood in
    the folder it left. Anything that cannot be decided is a no."""
    from django.core.exceptions import ObjectDoesNotExist

    from .models import VaultFile

    try:
        file_id = int(event.get("file"))
    except (TypeError, ValueError):
        return False
    try:
        if event.get("kind") == REMOVED:
            reader = event.get("reader") or {}
            vault_file = VaultFile(pk=file_id, owner_id=reader.get("owner"),
                                   is_public=bool(reader.get("public")),
                                   bucket_id=reader.get("bucket"),
                                   directory_id=event.get("directory"))
        else:
            vault_file = (VaultFile.objects.select_related("bucket", "directory")
                          .filter(pk=file_id).first())
        return access.may_read(user, vault_file)
    except ObjectDoesNotExist:      # the bucket or the folder went meanwhile
        return False


def _event(kind, file_id, directory_id, state=None) -> dict:
    from toto.core import live

    event = {"type": live.FOLDER, "kind": kind, "file": file_id, "directory": directory_id}
    if kind == REMOVED:
        state = state or {}
        event["reader"] = {"owner": state.get("owner_id"),
                           "public": bool(state.get("is_public")),
                           "bucket": state.get("bucket_id")}
    return event


def events_for(kind, vault_file, was) -> list:
    """The folder events one ``file_changed`` amounts to, as
    ``[(directory id, event)]``."""
    was = was or {}
    here = vault_file.__dict__.get("directory_id")
    before = was.get("directory_id")
    file_id = vault_file.pk
    out = []
    if kind in ("uploaded", "restored"):
        if here:
            out.append((here, _event(ADDED, file_id, here)))
    elif kind in ("replaced", "changed"):
        if here:
            out.append((here, _event(CHANGED, file_id, here)))
    elif kind in ("trashed", "deleted"):
        if before:
            out.append((before, _event(REMOVED, file_id, before, was)))
    elif kind == "moved":
        if before:
            out.append((before, _event(REMOVED, file_id, before, was)))
        if here:
            out.append((here, _event(ADDED, file_id, here)))
    return out


def publish(sender=None, file=None, kind="", was=None, **kwargs) -> None:
    """``signals.file_changed``'s listener: tell the folder's watchers."""
    from toto.core import live

    if file is None or not getattr(file, "pk", None):
        return
    for directory_id, event in events_for(kind, file, was):
        live.publish(folder_group(directory_id), event)


def connect() -> None:
    from .signals import file_changed

    file_changed.connect(publish, dispatch_uid="toto.vault.live.publish")
