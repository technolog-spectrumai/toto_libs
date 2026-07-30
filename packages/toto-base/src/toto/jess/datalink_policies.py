"""Jess is refused entirely — one model for a credential, one for a private log.

This module is required rather than optional: the per-app policy modules are what make
the registry complete, and ``datalink/validate.py`` treats a model with no policy as a
model nobody decided about. Refusing is a decision; silence is not.

It also carries forward the refusal that ``api.EmailService`` used to declare
(``api/datalink_policies.py``, removed when Jess absorbed it), because the reason it was
refused is a property of the design rather than of that model.
"""
from toto.datalink.registry import IDENTITY_REFUSE, STAGE_INFRA, SyncPolicy, register

register(SyncPolicy(
    "jess.EmailProvider", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "The SMTP password is a gervazy EncryptedSecret whose AEAD binds the secret's "
        "own local pk and strongbox, so ciphertext copied to a peer cannot be decrypted "
        "there — a replicated provider would be listed in the admin, look configured, "
        "and fail on its first send. Same shape as api.Connector, and the same reason "
        "the absorbed api.EmailService was refused before it."
    ),
))
register(SyncPolicy(
    "jess.MailMessage", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "An outbox is a log of who was emailed what, including bcc and message bodies. "
        "Replicating it would export recipient addresses and the contents of password "
        "reset mail to a peer that has no business holding either. Nothing on the "
        "receiver could act on the rows in any case: the delivery already happened, or "
        "already failed, on the sender."
    ),
))
