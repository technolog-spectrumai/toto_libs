"""What toto.core replicates: federations, and nothing else."""
from toto.datalink.registry import (
    IDENTITY_REFUSE,
    IDENTITY_UID,
    STAGE_INFRA,
    SyncPolicy,
    register,
)

register(SyncPolicy(
    "core.Federation", stage=STAGE_INFRA, identity=IDENTITY_UID,
    unique_guards=(("name",),),
    fields=("name", "description", "active"),
    notes=(
        "Not in the stated scope on its own, but socialhub.Community.federation points "
        "here. Registering three scalar fields is strictly better than dropping the "
        "reference and silently losing every community's federation membership. "
        "`logo` is omitted: no file bytes travel. `created_at` is auto_now_add."
    ),
))

register(SyncPolicy(
    "core.Platform", stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "This row IS the instance's own identity — domain, site name, author, branding, "
        "rate limits. Copying it would make the receiver advertise the peer's domain, "
        "and put two rows in contention for active=True. It also carries no unique "
        "constraint at all, so there is no natural key to match on even if we wanted to."
    ),
))

for _label in ("core.Font", "core.ColorMix", "core.Theme"):
    register(SyncPolicy(
        _label, stage=STAGE_INFRA, identity=IDENTITY_REFUSE,
        refuse_reason=(
            "Host appearance, out of the agreed scope. Each has a unique `name` and "
            "would be straightforward to add as an `appearance` stage if wanted."
        ),
    ))
