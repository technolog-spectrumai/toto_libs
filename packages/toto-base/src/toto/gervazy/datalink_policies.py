"""gervazy is refused wholesale. This is the boundary the whole feature is built around.

Every key here is rooted in a per-instance random salt: UserStrongbox.salt feeds
Argon2id to derive the key that unwraps everything below it. The receiver's strongbox
for the same person has a different salt, so the peer's ciphertext is undecryptable
there by construction — not by policy. EncryptedSecret goes further and binds its own
auto-increment pk into the AEAD's associated data, so a row that lands on a different
pk fails to decrypt even if the key were somehow identical.

Two more reasons a copied row would not even save: every model here calls
full_clean() from save(), and those clean() methods enforce cross-row invariants a
copied row violates (a data key whose master key is not active; a salt that is not
exactly 16 bytes).
"""
from toto.datalink.registry import IDENTITY_REFUSE, STAGE_INFRA, SyncPolicy, register

_KEYS = (
    "gervazy.UserStrongbox", "gervazy.VaultMasterKey", "gervazy.WrappedDataKey",
    "gervazy.EncryptedSecret", "gervazy.EncryptedFile", "gervazy.EncryptedFileChunk",
    "gervazy.EncryptedPrivateKey",
)
for _label in _KEYS:
    register(SyncPolicy(
        _label, stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
        refuse_reason=(
            "An encryption key or ciphertext rooted in this instance's own random "
            "per-strongbox salt. Undecryptable on any other instance."
        ),
    ))

register(SyncPolicy(
    "gervazy.PersonSigningKey", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "Its encrypted_private_key is a PROTECT FK, so it cannot even be copied with a "
        "null key. Replicating it would also be wrong: verification must fail closed on "
        "a host that does not hold the private half."
    ),
))
register(SyncPolicy(
    "gervazy.CryptoAuditLog", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "A record of cryptographic operations that happened on the peer, append-only "
        "and unbounded. Its save() also rejects text containing credential-shaped "
        "substrings, so a bulk import would hard-fail."
    ),
))
