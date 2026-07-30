"""toto.api is refused entirely: its secrets live in gervazy and cannot travel."""
from toto.datalink.registry import IDENTITY_REFUSE, STAGE_INFRA, SyncPolicy, register

register(SyncPolicy(
    "api.Connector", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "Host integration config whose credentials are gervazy rows — api_secret and "
        "signing_key are both PROTECT FKs into EncryptedSecret/EncryptedPrivateKey. "
        "A copied connector would be listed in the UI and fail on first use, because "
        "decrypt_api_secret raises when the FK is null and the ciphertext could not be "
        "decrypted on the receiver anyway (its AAD embeds the row's own local pk)."
    ),
))
