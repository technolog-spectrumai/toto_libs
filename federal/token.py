import jwt
import requests
from datetime import datetime, timedelta


class TokenService:
    def __init__(self, identity_provider):
        self.identity_provider = identity_provider
        self.rsa_key = identity_provider.rsa_key
        self.issuer_url = identity_provider.issuer_url
        self.audience = identity_provider.audience

    def issue_token(self, federated_identity, payload: dict, expires_in=300, algorithm='RS256') -> str:
        if not self.rsa_key:
            raise ValueError("Issuer has no RSA key assigned for signing.")
        now = datetime.utcnow()
        payload.update({
            'sub': federated_identity.subject,
            'iss': self.issuer_url,
            'aud': self.audience,
            'iat': now,
            'exp': now + timedelta(seconds=expires_in)
        })
        token = jwt.encode(
            payload,
            self.rsa_key.get_private_key(),
            algorithm=algorithm,
            headers={'kid': self.rsa_key.key_id}
        )
        return token

    def verify_token(self, token: str) -> dict | None:
        keys = self.fetch_jwks()
        try:
            header = jwt.get_unverified_header(token)
            key = next((k for k in keys if k['kid'] == header['kid']), None)
            if not key:
                raise ValueError("Matching key not found in JWKS")
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
            decoded = jwt.decode(
                token,
                public_key,
                algorithms=[key['alg']],
                audience=self.audience,
                issuer=self.issuer_url
            )
            return decoded
        except Exception:
            return None

    def fetch_jwks(self) -> list[dict]:
        jwks_url = f"{self.issuer_url.rstrip('/')}/.well-known/jwks.json"
        response = requests.get(jwks_url)
        if response.status_code != 200:
            raise ValueError(f"Failed to fetch JWKS from {jwks_url}")
        jwks = response.json()
        return jwks.get('keys', [])
