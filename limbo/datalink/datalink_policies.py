"""Policies datalink declares on behalf of apps that are not ours to edit.

``django.contrib.auth`` is the important one. Its refusal is the mechanism by which
"datalink never touches user accounts" is enforced rather than merely intended: every
FK in the registry must resolve to a non-refused target, so any model still pointing
at ``auth.User`` fails ``validate_registry`` at startup instead of shipping a
reference that means a different person on the receiver.
"""
from .registry import IDENTITY_REFUSE, STAGE_PEOPLE, SyncPolicy, register

# --- accounts: never, in either direction ---------------------------------
register(SyncPolicy(
    "auth.User", stage=STAGE_PEOPLE, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "Accounts are federation's job, not datalink's. datalink never creates, "
        "updates or deletes a user row: a password hash is a credential that must not "
        "become valid on a second host, is_staff/is_superuser would be privilege "
        "escalation by data transfer, and a session is signed with one host's "
        "SECRET_KEY. A replicated people.Person arrives with `user` unset and is "
        "claimed by sso_client._link_person on the person's first federated sign-in."
    ),
))
register(SyncPolicy(
    "auth.Group", stage=STAGE_PEOPLE, identity=IDENTITY_REFUSE,
    refuse_reason="Group membership is a local authorization decision.",
))
register(SyncPolicy(
    "auth.Permission", stage=STAGE_PEOPLE, identity=IDENTITY_REFUSE,
    refuse_reason="Rows are bound to this install's ContentTypes.",
))

# --- datalink's own tables ------------------------------------------------
# Peer credentials and run history. Replicating them would hand a peer the keys to
# every other peer, and a run's audit trail belongs to the host that performed it.
for _label in (
    "datalink.DatalinkGrant",
    "datalink.DatalinkPeer",
    "datalink.DatalinkRun",
    "datalink.DatalinkStageRun",
    "datalink.DatalinkMergeBase",
    "datalink.DatalinkConflict",
    "datalink.DatalinkIdentityMap",
):
    register(SyncPolicy(
        _label, stage=STAGE_PEOPLE, identity=IDENTITY_REFUSE,
        refuse_reason=(
            "datalink's own bookkeeping: peer credentials, merge state and audit "
            "trail, all specific to this host."
        ),
    ))
