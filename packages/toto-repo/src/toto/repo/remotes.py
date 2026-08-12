"""Who, if anyone, can supply credentials for a remote URL.

``toto.repo`` pushes to whatever origin a repository holds, with no stored
credentials: a private remote surfaces git's own auth error, which is honest.
That is the whole story on a host where nothing else is installed.

A host CAN have something that knows better. ``toto.gitea`` runs a Gitea the
platform itself deployed, mints a per-user token for it and can hand that token
over for exactly the URLs that belong to that Gitea. This module is the seam it
plugs into — the ``<app>/fees.py`` and ``<app>/taxes.py`` shape, a registry
populated at import time by whoever is installed.

Two properties matter and are the reason this is a registry rather than an
import:

* ``toto.repo`` never names ``toto.gitea``. A host that installs only the local
  half has no dead import and no branch that mentions a Gitea it does not have —
  ``credentials_for`` simply finds no claimant and returns the empty pair, the
  same answer a custom remote has always got.
* A provider claims by URL, not by a flag on the repo. The pre-split model kept
  a Gitea owner/name pair beside the URL and a rule about which one won; claiming
  makes the URL the single fact, and a second provider (someone else's forge)
  would slot in without touching this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RemoteProvider:
    """One app's offer to authenticate some remotes.

    ``claims(url)`` answers "is this mine?" and must be cheap and total — it runs
    on every push and pull, and a provider that raises here would break pushes to
    remotes it does not even own. ``credentials(url, user)`` returns
    ``(username, token)`` and may raise: by then it has claimed the URL, so a
    failure to mint is a real error the user needs to see.
    """

    name: str
    claims: Callable[[str], bool]
    credentials: Callable[[str, object], "tuple[str, str]"]


class RemoteRegistry:
    def __init__(self):
        self._providers: dict[str, RemoteProvider] = {}

    def register(self, provider: RemoteProvider) -> None:
        # Keyed by name and last-wins, deliberately: registration happens in
        # AppConfig.ready(), which Django calls more than once in a test
        # process, so refusing a duplicate would make the second call an error
        # for doing nothing new.
        self._providers[provider.name] = provider

    def provider_for(self, url: str) -> RemoteProvider | None:
        for provider in self._providers.values():
            try:
                if provider.claims(url):
                    return provider
            except Exception:
                # A broken claimant must not take down pushes to remotes it
                # does not own. It simply does not claim.
                continue
        return None

    def names(self) -> list[str]:
        return sorted(self._providers)


registry = RemoteRegistry()


def credentials_for(url: str, user) -> tuple[str, str]:
    """``(username, token)`` for this URL, or the empty pair.

    The empty pair means "push it verbatim" — not an error. It is what every
    custom remote gets and what EVERY remote gets on a host with no provider
    installed, which is both hosts in this monorepo today.
    """
    if not url:
        return "", ""
    provider = registry.provider_for(url)
    if provider is None:
        return "", ""
    return provider.credentials(url, user)
