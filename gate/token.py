import datetime
import jwt
from django.utils import timezone
from django.contrib.auth import get_user_model
from gate.models import RefreshToken

User = get_user_model()


class TokenGuard:
    """
    Minimal JWT guard for access/refresh tokens.
    Handles:
    - Access token issuing
    - Refresh token issuing & persistence
    - Token verification
    - Refresh flow
    """

    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm

    # --- JWT issuing ---
    def issue_access_token(self, user: User, expires_in: int = 900) -> str:
        now = timezone.now()
        payload = {
            "sub": str(user.id),
            "username": user.get_username(),
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=expires_in)).timestamp()),
            "scope": "access"
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def issue_refresh_token(self, user: User, expires_in: int = 604800) -> str:
        now = timezone.now()
        payload = {
            "sub": str(user.id),
            "username": user.get_username(),
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=expires_in)).timestamp()),
            "scope": "refresh"
        }
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

        # persist refresh token
        rt = RefreshToken(
            user=user,
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

        user = User.objects.get(id=payload["sub"])
        return self.issue_access_token(user)

    @staticmethod
    def get_user_from_refresh_token(refresh_token: str) -> User | None:
        """
        Decode refresh token without verifying signature to extract user_id.
        Returns the User or None if invalid.
        """
        payload = jwt.decode(refresh_token, options={"verify_signature": False})
        user_id = payload.get("sub")
        if not user_id:
            return None
        return User.objects.get(pk=user_id)
