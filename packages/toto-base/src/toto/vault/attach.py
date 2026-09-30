"""Referencing a vault file from another app, without widening access to it.

**The rule, in one sentence: a foreign key to ``VaultFile`` is only as
trustworthy as the form that set it.**

An owning app that lets a user submit a `VaultFile` pk — a ModelForm field, an
API body, an admin dropdown — and then renders or serves that file has built a
private-file disclosure. The pk space is small and guessable, nothing about a
`ForeignKey` checks who may read the target, and the owning app's own
permission check ("may this person edit this lead?") answers a different
question from "may this person read that file?".

``toto.cyprian.bridge`` documents the same trap from the other side, for
document ownership:

    "`claim(vault_file)` is the trust anchor, and it must be answered from data
    the REQUESTER CANNOT WRITE."

This module is that answer for attachments. Two functions, and the discipline
is to use both:

* :func:`validate_reference` at the WRITE door — the only place a submitted pk
  becomes a stored one.
* :func:`readable` at every render and download — because access is not
  frozen at attach time. A file shared with somebody in March can be unshared
  in April, and the row referencing it does not move.

**Why not a generic Attachment model.** Every app that has needed this so far
has wanted its own columns beside the reference (`mail.MailAttachment` keeps
the filename and content type; kanban's keeps a label). A through-model per app
with a FK to ``vault.VaultFile`` is the shape that works, and it costs one
table. What this module supplies is the two checks, not the table.
"""

from __future__ import annotations

from django.http import Http404


def validate_reference(user, pk, *, file_types=None):
    """The VaultFile this user may ATTACH, or 404.

    ``include_public=False`` is the whole point, and it is deliberately
    stricter than :func:`toto.vault.access.may_read`. A public file is
    *readable* by everybody, but attaching one to a record copies a reference
    into a place with its own audience — and "I can see it" is a weaker claim
    than "it is mine to put somewhere". Owned files, files in one's own
    buckets, and files in a shared directory qualify; a public file belonging
    to a stranger does not.

    404 rather than 403, matching the vault's own doors: a refusal that
    distinguishes "no such file" from "not yours" tells a stranger which pks
    exist.

    ``file_types`` narrows to a tuple like ``("pdf", "image")`` when the owning
    app only makes sense for some of them; the refusal is identical either way.
    """
    from toto.vault.filetree import accessible_files

    if pk in (None, "", 0):
        raise Http404("No file.")
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        raise Http404("No such file.") from None

    found = (accessible_files(user, file_types=file_types, include_public=False)
             .filter(pk=pk).first())
    if found is None:
        raise Http404("No such file.")
    return found


def readable(user, vault_file) -> bool:
    """May this user still read an ALREADY-attached file?

    Called at render and download time, every time. The attachment recorded a
    decision made once; this asks the vault the live question, so a file that
    has since been unshared, moved to another bucket or made private stops
    appearing to people who can no longer read it.

    Delegates to the vault's own five-clause rule rather than restating it —
    ``toto.vault.access`` says why a second copy is dangerous, and it is the
    same reason here.
    """
    from toto.vault.access import may_read

    # A trashed file is a missing file (2026-10-01): the foreign key still
    # finds it — the base manager sees the trash — so it is refused here.
    if not vault_file or getattr(vault_file, "trashed_at", None) is not None:
        return False
    return may_read(user, vault_file)


def visible(user, attachments, *, attr="vault_file"):
    """Filter an iterable of through-rows down to the ones still readable.

    The list form of :func:`readable`, for a template that renders an
    attachment list. Evaluates the iterable — callers holding a large queryset
    should page it first.
    """
    return [row for row in attachments
            if readable(user, getattr(row, attr, None))]
