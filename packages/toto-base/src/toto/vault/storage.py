"""Vault bytes are not web-served. This is what makes that structural.

**The hole this closes.** `VaultFile.file` was a plain `FileField` under
`MEDIA_ROOT`, and the deployed nginx aliases `/media/` straight onto that
directory with `Cache-Control: public` and no auth. So
`https://host/media/vault/files/payroll.pdf` needed no login at all — and the
name is guessable, because `FileSystemStorage.get_available_name` only appends a
suffix on collision, so the first upload of `payroll.pdf` is at exactly that
path. Every view-level permission check in the codebase was bypassable by
constructing a URL.

The repo already knew: `forum/api_views.py` stores attachments OUTSIDE
`MEDIA_ROOT` "precisely so that nginx cannot hand them out unauthenticated", and
cyprian, aralia and ambrosia each route private output through a view for the
same stated reason. The vault — which holds everything — never got the
treatment.

**How this fixes it, and why this shape.** The storage keeps the same
`location`, so **every existing file stays exactly where it is** and nothing has
to be moved or re-uploaded. What changes is `base_url`: with none,
`FieldFile.url` RAISES instead of returning a public path. That is the point —
a leak now fails loudly at the moment somebody writes the leak, rather than
quietly working. Reading bytes goes through `vault:public_file`, which asks
`toto.vault.access.may_read` first.

The nginx template drops `/media/vault/` to match. Both halves are needed and
neither alone is enough: the config stops the serving, and this stops anything
from generating a link to it in the first place.

**`location` is a plain property, not the inherited `cached_property`, and that
is not a detail.** Django caches it, and a storage built once at import would
freeze whatever `MEDIA_ROOT` was then — which would silently break every
`@override_settings(MEDIA_ROOT=...)` in this repo's tests (the idiom exists
because the real media dir is a root-owned bind mount). Resolving per access
costs one `settings` lookup and keeps the storage honest about where files are.

`VAULT_ROOT` lets a host put vault bytes on a different disk entirely. Unset —
the default — it stays under `MEDIA_ROOT`, so **no existing file moves**; what
changes is only that nothing can address them over HTTP.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateFileSystemStorage(FileSystemStorage):
    """Files on disk, reachable by no URL."""

    @property
    def base_location(self):
        return getattr(settings, "VAULT_ROOT", None) or settings.MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        """Always raises. That IS the feature.

        `FileSystemStorage` cannot express "no URL" by configuration —
        `base_url=None` means "fall back to MEDIA_URL", which is exactly the
        public path we are closing. So the refusal is explicit, and it names
        the door to use instead.
        """
        raise ValueError(
            "Vault files are not web-served. Use VaultFile.get_public_url(), "
            "which goes through vault:public_file and checks "
            "toto.vault.access.may_read first."
        )


def private_storage():
    """The storage instance for `VaultFile.file`.

    A callable rather than an instance so migrations serialize a dotted path
    and not a frozen path from whichever machine ran `makemigrations`.
    """
    return PrivateFileSystemStorage()
