import secrets
from dataclasses import dataclass

from .models import SSORelyingParty


@dataclass(frozen=True)
class ProvisionedRelyingParty:
    relying_party: SSORelyingParty
    client_secret: str | None


class RelyingPartyProvisioningError(ValueError):
    pass


def create_relying_party(
    *,
    name,
    redirect_uris,
    trusted=False,
    public=False,
    scopes="openid email profile",
    client_id=None,
    raw_secret=None,
    force_recreate=False,
):
    """Register (or re-register) a relying party, idempotently.

    ``force_recreate`` UPDATES an existing registration in place. It used to
    ``delete()`` it, which was a quiet disaster: ``SSOAuthorizationCode.client``
    and ``SSOAccessToken.client`` both cascade, and ``ingress_all`` runs from the
    container entrypoint on *every* start — so a routine restart (or an
    on-failure restart loop) revoked every live token of every seeded relying
    party, and changed the row's UUID underneath them. Callers want "make the
    registration match this", not "throw it away and make a new one".

    Deleting is still reachable, deliberately, via ``recreate_relying_party``.
    """
    client_id = client_id or secrets.token_urlsafe(24)
    relying_party = SSORelyingParty.objects.filter(client_id=client_id).first()

    if relying_party and not force_recreate:
        raise RelyingPartyProvisioningError(
            f"Client ID already exists: {client_id}. Use --force-recreate to replace it."
        )

    if relying_party is None:
        relying_party = SSORelyingParty(client_id=client_id)

    relying_party.name = name
    relying_party.redirect_uris = "\n".join(redirect_uris)
    relying_party.trusted = trusted
    relying_party.allowed_scopes = scopes
    relying_party.client_type = (
        SSORelyingParty.PUBLIC if public else SSORelyingParty.CONFIDENTIAL
    )

    client_secret = None
    if relying_party.client_type == SSORelyingParty.CONFIDENTIAL:
        # Re-derives the hash from the same deployment secret on a re-run, so the
        # value the relying party already holds keeps working.
        client_secret = relying_party.set_client_secret(raw_secret)

    relying_party.save()
    return ProvisionedRelyingParty(relying_party=relying_party, client_secret=client_secret)


def recreate_relying_party(*, client_id, **kwargs):
    """Delete a registration and issue a fresh one, cascading its live tokens.

    The rare, deliberate case: a leaked client secret with no way to rotate it
    on the relying-party side, or a client_id being repurposed. Everything
    routine should use :func:`create_relying_party`.
    """
    SSORelyingParty.objects.filter(client_id=client_id).delete()
    return create_relying_party(client_id=client_id, **kwargs)


def add_relying_party_arguments(parser):
    parser.add_argument("--name", required=True)
    parser.add_argument("--redirect-uri", action="append", required=True, help="Can be provided multiple times.")
    parser.add_argument("--trusted", action="store_true", help="Skip consent for this relying party.")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Create a public relying party. Public clients must use PKCE.",
    )
    parser.add_argument("--scopes", default="openid email profile")
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--raw-secret", default=None, help="Use a specific client secret (dev only).")
    parser.add_argument("--force-recreate", action="store_true", help="Delete and recreate relying party if --client-id exists.")


def create_relying_party_from_options(options):
    return create_relying_party(
        name=options["name"],
        redirect_uris=options["redirect_uri"],
        trusted=options["trusted"],
        public=options["public"],
        scopes=options["scopes"],
        client_id=options["client_id"],
        raw_secret=options["raw_secret"],
        force_recreate=options["force_recreate"],
    )
