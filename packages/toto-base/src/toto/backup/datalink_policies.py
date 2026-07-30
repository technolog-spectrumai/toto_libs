"""toto.backup is refused: instance-scoped, and it holds bearer credentials."""
from toto.datalink.registry import IDENTITY_REFUSE, STAGE_INFRA, SyncPolicy, register

register(SyncPolicy(
    "backup.BackupProfile", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "A OneToOne on the per-instance core.Platform, carrying a gervazy-backed "
        "signing key that cannot function on another host."
    ),
))
register(SyncPolicy(
    "backup.StoredBackup", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "Holds magic_token and api_key_hash — bearer credentials for pulling this "
        "instance's backup archives. Replicating them would hand a peer the ability to "
        "download this host's whole database."
    ),
))
