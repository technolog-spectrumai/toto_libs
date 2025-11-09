import base64
from federal.models import FederatedIdentity, Challenge, FederalAuthGateway


class FederalGuard:
    """
    DID Authentication guard.
    Uses a FederalAuthGateway to access signing secrets.
    Handles:
    - Challenge issuance
    - Signature verification
    (token lifecycle skipped for now)
    """

    def __init__(self, gateway: FederalAuthGateway):
        self.gateway = gateway
        # pull key material from linked SecretKey
        self.secret_key = gateway.key_material
        # algorithm could later be stored in SecretKey or gateway
        self.algorithm = "HS256"

    def initiate_login(self, identity: FederatedIdentity) -> Challenge:
        """
        Create and return a new Challenge for the given identity.
        """
        challenge = Challenge(identity=identity)
        challenge.save()
        return challenge

    def verify_login(self, identity: FederatedIdentity, signature_b64: str) -> bool:
        """
        Verify the latest challenge for the identity using the provided signature.
        """
        try:
            challenge = identity.challenges.latest("issued_at")
        except Challenge.DoesNotExist:
            return False

        signature = base64.b64decode(signature_b64)
        return challenge.verify(signature)
