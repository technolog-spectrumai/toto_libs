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
    client_id = client_id or secrets.token_urlsafe(24)
    existing = SSORelyingParty.objects.filter(client_id=client_id).first()
    if existing:
        if force_recreate:
            existing.delete()
        else:
            raise RelyingPartyProvisioningError(
                f"Client ID already exists: {client_id}. Use --force-recreate to replace it."
            )

    relying_party = SSORelyingParty(
        name=name,
        client_id=client_id,
        redirect_uris="\n".join(redirect_uris),
        trusted=trusted,
        allowed_scopes=scopes,
        client_type=SSORelyingParty.PUBLIC if public else SSORelyingParty.CONFIDENTIAL,
    )

    client_secret = None
    if relying_party.client_type == SSORelyingParty.CONFIDENTIAL:
        client_secret = relying_party.set_client_secret(raw_secret)

    relying_party.save()
    return ProvisionedRelyingParty(relying_party=relying_party, client_secret=client_secret)


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
