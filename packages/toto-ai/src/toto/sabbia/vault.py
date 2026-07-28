"""Back-compat re-export: this vault helper now lives in toto-base.

It was always generic Gervazy plumbing — its only dependencies are
``toto.gervazy.crypto`` and ``toto.gervazy.models``, and ``toto.core.env``
already owns the ``SABBIA_VAULT_PASSWORD`` default. It sat in toto-ai only for
historical reasons, which made ``toto.api`` — a CORE app installed everywhere —
depend on toto-ai being pinned. A host that does not install toto-ai (aurelian,
and now zenobia) hit ModuleNotFoundError when saving an API connector secret.

The strongbox name, owner username and setting name are unchanged, so deployed
strongbox rows and .env files keep working.
"""

from toto.gervazy.vault import *  # noqa: F401,F403
from toto.gervazy.vault import (  # noqa: F401  — names the star-import may skip
    VaultUnavailable,
    store_secret,
)
