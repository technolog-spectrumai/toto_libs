"""What this app offers ``toto.repo``: credentials for its own remotes.

Discovered, not imported — ``RepoConfig.ready()`` calls
``autodiscover_plugins("remotes")``, so ``toto.repo`` never names this module
and a host that installs only the local half simply has nothing to find. The
import below is guarded for the mirror case: a host with ``toto.gitea`` and no
``toto.repo`` (zenobia, after the split) has nothing to register WITH, and must
not fail its system check over it.
"""

from __future__ import annotations

from django.conf import settings

PROVIDER_NAME = "Gitea"


def _claims(url: str) -> bool:
    """URLs belonging to the Gitea this platform deployed, and only those.

    Gated on GITEA_ENABLED as well as the prefix: a host whose sidecar is off
    has no tokens to mint, and claiming a URL it cannot authenticate would turn
    a push that would have worked verbatim into a provisioning error.
    """
    if not getattr(settings, "GITEA_ENABLED", False):
        return False
    from . import client

    return client.owns(url)


def _credentials(url: str, user) -> tuple[str, str]:
    """Provision on demand. Reached only for a URL this provider claimed, so a
    failure here is real and must surface rather than degrade to a bare push:
    the user asked for the platform's own forge and did not get it."""
    from . import client

    account = client.ensure_account(user)
    return account.username, account.get_token()


try:
    from toto.repo.remotes import RemoteProvider, registry
except ImportError:  # toto.repo is not installed here — nothing to offer.
    pass
else:
    registry.register(RemoteProvider(
        name=PROVIDER_NAME, claims=_claims, credentials=_credentials))
