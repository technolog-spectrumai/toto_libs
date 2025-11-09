import base64
from federal.models import FederatedIdentity
from gate.models import AuthGateway, Challenge


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
        signature = base64.b64decode(signature_b64)
        return challenge.verify(signature)
