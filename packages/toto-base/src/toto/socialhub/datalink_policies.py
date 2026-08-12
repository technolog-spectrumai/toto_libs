"""Communities, their news and their charters.

`Community` is the one model in the whole scope with a real modification stamp
(`updated_at` is auto_now), so it is also the only place a both-changed conflict can be
broken by a timestamp — and even then the engine records that it did so rather than
resolving silently.

Note what is NOT here. `ConstitutionSignature` is refused: without gervazy the
receiver cannot verify a signature made on the peer, because
``SigningService.verify`` only ever consults local PersonSigningKey rows — yet
``is_cryptographically_signed`` would still render a "cryptographically signed" badge,
since it only checks that two text fields are non-empty. Replicating the row would
make the receiver assert an authenticity it cannot check.
"""
from toto.datalink.registry import (
    IDENTITY_NATURAL,
    IDENTITY_REFUSE,
    IDENTITY_UID,
    STAGE_COMMUNITIES,
    STAGE_CONTENT,
    STAGE_MEMBERSHIP,
    SyncPolicy,
    register,
)

register(SyncPolicy(
    "socialhub.Community", stage=STAGE_COMMUNITIES, identity=IDENTITY_UID,
    unique_guards=(("slug",), ("email",)),
    fields=(
        "name", "slug", "org_type", "location", "territory", "established_year",
        "head", "is_autonomous", "email", "is_foreign", "is_federal_tribe",
        "parent", "federation",
    ),
    m2m=("senior_members",), m2m_stage=STAGE_MEMBERSHIP,
    parent_field="parent",
    timestamp_field="updated_at",
    notes=(
        "`head` points at people.Person and is nullable, which is what stops "
        "Person<->Community being a cycle: Person has no FK to Community, only the "
        "`communities` M2M, so people -> communities -> membership needs no deferral. "
        "There is no `email_service` field any more: api.EmailService was absorbed "
        "into toto.jess, whose EmailProvider is refused for the same reason its "
        "predecessor was — the SMTP password is a gervazy row whose AAD binds its own "
        "pk, so a copied provider is listed and dead. `logo` is omitted (no bytes). "
        "slug is written explicitly so Community.save()'s slug derivation never runs."
    ),
))

register(SyncPolicy(
    "socialhub.CommunityPrivilege", stage=STAGE_COMMUNITIES, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "Authorization by data transfer. A privilege row grants real rights — the "
        "chain graph, the administrata view, publishing news anywhere, operating "
        "the mint — to every member of its community, and sets what they owe in "
        "head tax. "
        "datalink already refuses auth.User and auth.Group with the words "
        "'is_staff/is_superuser would be privilege escalation by data transfer', "
        "and this row is the same substance one level up: replicating it would "
        "let a peer's admin grant rights on THIS host by editing their own "
        "database. Each host decides its own grants, in its own admin."
    ),
))
for _label in ("socialhub.SocialhubUsageEvent", "socialhub.SocialhubQuotaPolicy"):
    register(SyncPolicy(
        _label, stage=STAGE_COMMUNITIES, identity=IDENTITY_REFUSE,
        refuse_reason=(
            "Host-local metering, refused for the same reason vault's pair is: "
            "a usage event is the only record that something was charged, so "
            "copying one would bill the receiver's member for the peer's day. "
            "This pair exists solely to carry the head tax's daily trail."
        ),
    ))

register(SyncPolicy(
    "socialhub.Station", stage=STAGE_COMMUNITIES, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "An office is authorization and a salary, and both are local. A station "
        "grants the same rights a privilege row does — to one named holder — and "
        "carries a stipend the FEDERAL TREASURY OF THIS HOST pays: replicating "
        "one would let a peer appoint an officer here, and have us pay them. "
        "Each host appoints its own, in its own admin. (It also names a Person "
        "and a Community, so it would need a stage after both; refusing costs "
        "nothing because there is nothing to sync.)"
    ),
))

register(SyncPolicy(
    "socialhub.CommunityNewsTopic", stage=STAGE_CONTENT, identity=IDENTITY_UID,
    unique_guards=(("name",), ("slug",)),
    fields=("name", "slug"), bulk_safe=True,
    notes="Declared before CommunityNewsPost: its `topics` M2M lands in this stage too.",
))

register(SyncPolicy(
    "socialhub.CommunityNewsPost", stage=STAGE_CONTENT, identity=IDENTITY_UID,
    fields=("title", "content", "order", "author", "community", "visibility"),
    m2m=("topics",), m2m_stage=STAGE_CONTENT,
    timestamp_field="updated_at",
    notes=(
        "`content` is a TrixEditorField — rich text stored as a string, so it "
        "serialises as one. `community` is NOT NULL, so a post whose community failed "
        "to resolve is withheld. `visibility` travels: a post restricted to a community "
        "must not become public on the receiver."
    ),
))

register(SyncPolicy(
    "socialhub.Constitution", stage=STAGE_CONTENT,
    identity=IDENTITY_NATURAL, natural_key=("slug",),
    unique_guards=(("slug",),),
    fields=("community", "title", "slug", "body", "version", "is_active"),
    timestamp_field="updated_at",
    notes=(
        "No uid — Constitution is not a DomainEntity — so its unique slug is the "
        "identity. Written explicitly so save()'s slug derivation never runs."
    ),
))

register(SyncPolicy(
    "socialhub.ConstitutionSignature", stage=STAGE_CONTENT, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "A signature the receiver cannot verify. SigningService.verify iterates only "
        "local PersonSigningKey rows, so every signature made on the peer returns "
        "False — while is_cryptographically_signed renders a 'signed' badge from two "
        "non-empty text fields, and nothing in the UI calls verify at all. Copying "
        "these rows would assert an authenticity this instance cannot check. Making "
        "them portable needs a public_key_pem snapshot plus verify_with_public_key "
        "(the pattern toto-economy's ledger already uses) — a separate change."
    ),
))

register(SyncPolicy(
    "socialhub.MembershipApplication", stage=STAGE_CONTENT, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "An in-flight workflow, not durable content: a unique email, a unique 6-digit "
        "verification code and an expiry. Importing half-finished applications mints "
        "invitations on the receiver."
    ),
))
register(SyncPolicy(
    "socialhub.ReferenceRequest", stage=STAGE_CONTENT, identity=IDENTITY_REFUSE,
    refuse_reason=(
        "Its save() ACTIVATES a User and CREATES a Person when the status becomes "
        "accepted. Importing these rows would mint accounts, which datalink must never "
        "do. Listed in registry.SAVE_SIDE_EFFECT_MODELS so validation enforces this."
    ),
))
