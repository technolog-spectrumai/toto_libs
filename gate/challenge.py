import base64
from federal.models import FederatedIdentity
from gate.models import Challenge


class ChallengeGuard:
    """
    Handles challenge issuance and verification.
    """

    @staticmethod
    def initiate_login(identity: FederatedIdentity) -> Challenge:
        challenge = Challenge(identity=identity)
        challenge.save()
        return challenge

    @staticmethod
    def verify_login(identity: FederatedIdentity, signature_b64: str) -> bool:
        try:
            challenge = identity.challenges.latest("issued_at")
        except Challenge.DoesNotExist:
            return False

        try:
            # Add "===" to ensure enough padding
            signature = base64.urlsafe_b64decode(signature_b64.strip())
        except Exception:
            return False

        return challenge.verify(signature)
