"""A message's image: checked, sealed, kept by the vault, read by one door.

**What is taken.** A JPEG, PNG, GIF or WebP of at most ``MAX_BYTES``, told by
its first bytes (``sniff``) and never by the sender's word or the file's
name: the type that is stored, and that the image door answers with, is the
one the bytes said. Anything else is refused, so a page, an SVG or an Office
file called ``.png`` is never stored and never served.

**Where it is kept.** Sealed under the channel's key (``sealing``, kind
``att``, bound to the channel and the message) and stored through the vault
as a ``VaultFile`` in the channel's bucket: owner the member who posted,
``is_encrypted`` set, never public, key ``forum-<message id>``. The forum
owns exactly the files its message rows point at; any other file in that
bucket is not the forum's, and no function here reads or removes one.

**Who reads it.** ``open_image`` hands the bytes to the image door, which
has asked ``access.may_read`` first. The vault's own doors can give the
file's owner or a superuser the ciphertext only.
"""

from __future__ import annotations

import hashlib
import logging

from django.utils.translation import gettext as _

from . import channels, sealing

log = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024

#: The types taken, and the ending a stored file is named with.
TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}

#: The mark on a vault file the forum made (beside the message row's link).
NOTE = "toto.forum"


class ImageRefused(Exception):
    """Not an image this door takes. ``status_code`` and a sentence."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def sniff(data: bytes) -> str:
    """The image type the first bytes say, or ""."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def read_upload(uploaded) -> tuple[bytes, str]:
    """``(bytes, type)`` of an uploaded file, or ``ImageRefused``."""
    size = getattr(uploaded, "size", None)
    if size is not None and size > MAX_BYTES:
        raise ImageRefused(
            _("The image is too large. The most is %(size)s MB.")
            % {"size": MAX_BYTES // (1024 * 1024)}, 413)
    data = uploaded.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ImageRefused(
            _("The image is too large. The most is %(size)s MB.")
            % {"size": MAX_BYTES // (1024 * 1024)}, 413)
    if not data:
        raise ImageRefused(_("The image file is empty."), 400)
    mime = sniff(data)
    if not mime:
        raise ImageRefused(_("Send a JPEG, PNG, GIF or WebP image."), 415)
    return data, mime


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store(channel, user, message_id, data: bytes, mime: str, key: bytes):
    """Seal ``data`` and keep it in the channel's bucket. Returns the
    ``VaultFile``. Call inside the transaction that stores the message; the
    caller removes the bytes (``discard``) if that transaction fails."""
    from django.core.files.base import ContentFile

    from toto.vault.models import VaultFile
    from toto.vault.storage_backends import persist_upload

    bucket = channels.ensure_bucket(channel)
    sealed = sealing.seal_bytes(key, data, kind="att", channel_id=channel.pk,
                                message_id=message_id)
    name = f"forum-{message_id}.{TYPES[mime]}.sealed"
    vault_file = VaultFile(owner=user, title=name, key=f"forum-{message_id}",
                           file_type="image", is_encrypted=True, is_public=False,
                           bucket=bucket, notes=NOTE, file_size_bytes=len(sealed))
    persist_upload(vault_file, ContentFile(sealed, name=name))
    return vault_file


def discard(vault_file) -> None:
    """Unlink the bytes of a file whose row a rolled-back transaction took.
    Never raises: the message is already refused."""
    try:
        name = vault_file.file.name
        storage = vault_file.file.storage
        if name and storage.exists(name):
            storage.delete(name)
    except Exception:  # noqa: BLE001
        log.warning("forum: could not remove the bytes of a refused image", exc_info=True)


def open_image(message, key: bytes) -> bytes:
    """The image's bytes, opened. ``FileNotFoundError`` when the vault no
    longer has them; ``sealing.SealBroken`` when they are not this
    message's."""
    from toto.vault.storage_backends import read_file_bytes

    vault_file = message.attachment
    if vault_file is None or vault_file.trashed_at is not None:
        raise FileNotFoundError("This message has no image.")
    try:
        frame = read_file_bytes(vault_file)
    except OSError as exc:
        raise FileNotFoundError("The image's bytes are gone.") from exc
    return sealing.open_bytes(key, frame, kind="att", channel_id=message.channel_id,
                              message_id=message.id)


def drop(message) -> None:
    """Remove a message's image for good: the vault's row and its bytes
    (``vault.purge.purge_file``: row first, bytes after the commit). Only the
    file the message points at; nothing else in the bucket."""
    from toto.vault.purge import purge_file

    vault_file = message.attachment
    if vault_file is None:
        return
    message.attachment = None
    message.save(update_fields=["attachment"])
    purge_file(vault_file)
