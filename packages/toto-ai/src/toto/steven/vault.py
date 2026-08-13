"""Steven's at-rest credential vault — its own strongbox, its own passphrase.

    STEVEN_VAULT_PASSWORD ─Argon2id▶ UKEK ─unwrap▶ VMK ─unwrap▶ DEK ─AES-GCM▶ key

**This module is nine lines because the fourth copy became a helper instead.**
sabbia, sso_core and jess each carry a 150-line near-identical ``vault.py``; when
a fourth arrived, ``sso_core/vault.py:13-17`` had already written down what to do
— extract ``Strongbox(name, password_setting)`` into gervazy. That is
:mod:`toto.gervazy.strongbox`, and this is its first consumer. The three older
copies are untouched: porting them is a change with its own blast radius and does
not belong inside a feature.

One blast radius per secret domain: steven's key lives in ``steven-system``, not
in ``sabbia-system``, whose name is inherited from a retired app and whose
existing callers all assume that one box.

**Losing STEVEN_VAULT_PASSWORD is unrecoverable.** ``scripts/deploy.py`` mints it
once and reuses the existing value on every redeploy for exactly that reason, and
``scripts/test_deploy.py`` asserts it survives.
"""

from toto.gervazy.strongbox import Strongbox, VaultUnavailable  # noqa: F401

vault = Strongbox(
    name="steven-system",
    owner_username="steven-vault",
    password_setting="STEVEN_VAULT_PASSWORD",
    label="Steven",
)
