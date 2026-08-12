"""vault is refused entirely — the one group that owns by auth.User.

This is a scope decision, not a technical limit, and it is what makes "datalink never
touches user accounts" checkable rather than merely intended. Bucket.owner,
VaultFile.owner and VaultDirectory.owner are all FKs to auth.User, and both
`allowed_users` M2Ms target it too. With accounts out of scope those references cannot
be resolved on the receiver at all, so every row here would be withheld anyway.

Read this before re-adding any of it. VaultDirectory.allowed_users is a SECURITY
CONTROL with fail-open semantics: user_can_access treats an EMPTY whitelist as "allow
everyone". So a directory replicated without its whitelist does not merely lose data —
it opens a private folder to every authenticated user on the receiver. Any future vault
stage must write the directory and its ACL in the same transaction, and withhold the
directory entirely when a member cannot be resolved.

VaultFile bytes are a separate refusal that outlives this one: a file encrypted as text
or image is Fernet-sealed under Argon2id(password, strongbox.salt), and that salt is
random per instance, so copied ciphertext is permanently unopenable on the receiver.
Encrypted PDFs are the exception — the password-only PDF handler travels fine.
"""
from toto.datalink.registry import IDENTITY_REFUSE, STAGE_INFRA, SyncPolicy, register

_OWNED_BY_USER = (
    ("vault.Bucket", "owner is an auth.User FK"),
    ("vault.VaultFile", "owner is an auth.User FK; its bytes never travel either"),
    ("vault.VaultDirectory",
     "owner is an auth.User FK, and allowed_users is a fail-open ACL over auth.User"),
    ("vault.FileGateway",
     "grants upload rights via an allowed_users M2M over auth.User"),
)
for _label, _why in _OWNED_BY_USER:
    register(SyncPolicy(
        _label, stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
        refuse_reason=(
            f"Out of scope: {_why}. auth.User is refused, so the reference cannot be "
            f"resolved on the receiver. See this module's docstring before re-adding."
        ),
    ))

register(SyncPolicy(
    "vault.StorageProvider", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason="Host storage endpoint config; unreachable with Bucket refused.",
))
for _label in ("vault.BucketCopyLog", "vault.VaultUsageEvent", "vault.VaultQuotaPolicy"):
    register(SyncPolicy(
        _label, stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
        refuse_reason=(
            "Host-local audit or metering. Usage events are the only record of "
            "consumption, so copying them would double-count the peer's activity "
            "against the receiver's limits."
        ),
    ))

# Version history and editing locks. Refused for three separate reasons, any one
# of which would be sufficient.
#
# FileVersion hangs off a VaultFile, which is itself refused — so a version
# copied to a receiver would point at a file that is not there. Its author is an
# auth.User FK, out of scope like every other one here.
#
# VersionBlob is the bytes. VaultFile's own bytes never travel (see the module
# docstring); a version's are the same bytes at an earlier moment, and there is
# no reading under which the copy is allowed but the original is not.
#
# FileLock is host-local by definition: it says who is typing into a document on
# THIS server right now. Replicating it would export a fact that is already
# false by the time it lands, and could only ever lock a receiver's users out of
# their own documents.
register(SyncPolicy(
    "vault.FileVersion", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "History of a refused VaultFile, authored by a refused auth.User. The "
        "row would reference two things the receiver does not have."
    ),
))
register(SyncPolicy(
    "vault.VersionBlob", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "Document bytes at an earlier moment. VaultFile bytes never travel, and "
        "a version's are the same bytes — see this module's docstring."
    ),
))
register(SyncPolicy(
    "vault.FileLock", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "Says who is editing a document on THIS host right now. It is stale the "
        "moment it lands and could only lock the receiver's users out."
    ),
))
