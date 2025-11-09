import base64
import datetime
import jwt
from django.utils import timezone
from federal.models import FederatedIdentity, Challenge, FederalAuthGateway
from federal.models import RefreshToken


class FederalGuard:
    """
    DID Authentication guard.
    Uses a FederalAuthGateway to access signing secrets.
    Handles:
    - Challenge issuance
    - Signature verification
    - JWT access/refresh token lifecycle
    """

    def __init__(self, gateway: FederalAuthGateway):
        self.gateway = gateway
        self.secret_key = gateway.key_material
        self.algorithm = "HS256"

    # --- Challenge flow ---
    def initiate_login(self, identity: FederatedIdentity) -> Challenge:
        challenge = Challenge(identity=identity)
        challenge.save()
        return challenge

    def verify_login(self, identity: FederatedIdentity, signature_b64: str) -> bool:
        try:
            challenge = identity.challenges.latest("issued_at")
        except Challenge.DoesNotExist:
            return False
        signature = base64.b64decode(signature_b64)
        return challenge.verify(signature)

    # --- JWT issuing ---
    def issue_access_token(self, identity: FederatedIdentity, expires_in: int = 900) -> str:
        now = timezone.now()
        payload = {
            "sub": str(identity.id),
            "iss": identity.issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=expires_in)).timestamp()),
            "scope": "access"
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def issue_refresh_token(self, identity: FederatedIdentity, expires_in: int = 604800) -> str:
        now = timezone.now()
        payload = {
            "sub": str(identity.id),
            "iss": identity.issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=expires_in)).timestamp()),
            "scope": "refresh"
        }
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

        # persist refresh token
        rt = RefreshToken(
            identity=identity,
            token=token,
            expires_at=now + datetime.timedelta(seconds=expires_in)
        )
        rt.save()
        return token

    # --- Verification ---
    def verify_token(self, token: str, expected_scope: str = "access") -> dict | None:
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            if payload.get("scope") != expected_scope:
                return None
            return payload
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None

    # --- Refresh flow ---
    def refresh_access_token(self, refresh_token: str) -> str | None:
        payload = self.verify_token(refresh_token, expected_scope="refresh")
        if not payload:
            return None

        # check persisted refresh token
        try:
            rt = RefreshToken.objects.get(token=refresh_token, revoked=False)
        except RefreshToken.DoesNotExist:
            return None
        if rt.is_expired():
            return None

        identity = FederatedIdentity.objects.get(id=payload["sub"])
        return self.issue_access_token(identity)

    @staticmethod
    def get_identity_from_refresh_token(refresh_token: str) -> FederatedIdentity | None:
        """
        Decode refresh token without verifying signature to extract identity_id.
        Returns the FederatedIdentity or raises if invalid.
        """
        payload = jwt.decode(refresh_token, options={"verify_signature": False})
        identity_id = payload.get("sub")
        if not identity_id:
            return None
        # Let DoesNotExist propagate instead of hiding it
        return FederatedIdentity.objects.get(pk=identity_id)
