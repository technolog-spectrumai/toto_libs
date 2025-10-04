import jwt
import requests
from datetime import datetime, timedelta
from federal.models import LocalIdentityProvider, ExternalIdentityProvider
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Payload:
    subject: str
    issuer: str
    audience: str
    issued_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict:
        return {
            "sub": self.subject,
            "iss": self.issuer,
            "aud": self.audience,
            "iat": int(self.issued_at.timestamp()),
            "exp": int(self.expires_at.timestamp())
        }

    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    def create_token(self, private_key, key_id: str, algorithm: str = "RS256") -> str:
        return jwt.encode(
            self.to_dict(),
            private_key,
            algorithm=algorithm,
            headers={"kid": key_id}
        )

@dataclass
class Token(Payload):
    value: str  # JWT string

    def serialize(self) -> dict:
        return {
            "token": self.value,
            **self.to_dict(),
            "iat": self.issued_at.isoformat(),
            "exp": self.expires_at.isoformat()
        }

def sign_token(payload: Payload, private_key: str, key_id: str, algorithm: str = "RS256") -> Token:
    token_str = payload.create_token(
        private_key=private_key,
        key_id=key_id,
        algorithm=algorithm
    )
    return Token(
        value=token_str,
        subject=payload.subject,
        issuer=payload.issuer,
        audience=payload.audience,
        issued_at=payload.issued_at,
        expires_at=payload.expires_at
    )


class TokenService:
    def __init__(self, identity_provider):
        self.identity_provider = identity_provider
        self.issuer_url = identity_provider.issuer_url
        self.audience = identity_provider.audience
        self.token_lifetime = getattr(identity_provider, 'token_lifetime', 300)
        self.algorithm = 'RS256'

        if isinstance(identity_provider, LocalIdentityProvider):
            self.rsa_key = identity_provider.rsa_key
        else:
            self.rsa_key = None  # External providers don't store keys locally

    def issue_token(self, federated_identity) -> Token:
        if not self.rsa_key:
            raise ValueError("Cannot issue token: RSA key is missing or provider is external.")

        now = datetime.utcnow()
        exp = now + timedelta(seconds=self.token_lifetime)

        payload = Payload(
            subject=federated_identity.subject,
            issuer=self.issuer_url,
            audience=self.audience,
            issued_at=now,
            expires_at=exp
        )

        return sign_token(
            payload=payload,
            private_key=self.rsa_key.get_private_key(),
            key_id=self.rsa_key.key_id,
            algorithm=self.algorithm
        )

    def verify_token(self, token_str: str) -> dict | None:
        keys = self.fetch_jwks()
        try:
            header = jwt.get_unverified_header(token_str)
            key = next((k for k in keys if k['kid'] == header['kid']), None)
            if not key:
                raise ValueError("Matching key not found in JWKS")

            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
            decoded = jwt.decode(
                token_str,
                public_key,
                algorithms=[key['alg']],
                audience=self.audience,
                issuer=self.issuer_url
            )

            exp = decoded.get('exp')
            if exp and datetime.utcnow() > datetime.utcfromtimestamp(exp):
                return None  # Token is expired

            return decoded

        except jwt.ExpiredSignatureError:
            return None
        except Exception:
            return None

    def fetch_jwks(self) -> list[dict]:
        if isinstance(self.identity_provider, ExternalIdentityProvider):
            jwks_url = self.identity_provider.jwks_url
        elif isinstance(self.identity_provider, LocalIdentityProvider):
            return [self.rsa_key.to_jwk()]
        else:
            raise ValueError("Unsupported identity provider type")

        response = requests.get(jwks_url)
        if response.status_code != 200:
            raise ValueError(f"Failed to fetch JWKS from {jwks_url}")
        jwks = response.json()
        return jwks.get('keys', [])