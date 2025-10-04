from django.contrib.auth.models import User
from oya.ingress import IngressCommand
from django.utils.crypto import get_random_string
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from federal.models import (
    RSAKeyPair, TrustedIssuer,
    FederatedIdentity, UserFederationLink
)


class Command(IngressCommand):
    help = "Seed identity models with RSA keys, trusted issuers, federated identities, and links"

    def process(self, _):
        # 🔐 Generate RSA Key Pair
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ).decode()

        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

        rsa_key = RSAKeyPair.objects.create(
            key_id=get_random_string(12),
            public_key_pem=public_pem,
            private_key_pem=private_pem,
            issuer="https://your-project.example.com"
        )

        # 🌐 Create Trusted Issuer
        issuer = TrustedIssuer.objects.create(
            name="Demo Issuer",
            issuer_url="https://your-project.example.com",
            jwks_url="https://your-project.example.com/.well-known/jwks.json",
            audience="your-audience",
            trusted=True,
            rsa_key=rsa_key
        )

        # 👤 Create Federated Identity
        federated_identity = FederatedIdentity.objects.create(
            subject=get_random_string(16),
            issuer=issuer.issuer_url,
            email="demo@example.com",
            name="Demo User"
        )

        # 🔗 Link to Local User
        user, _ = User.objects.get_or_create(username="demo_user", defaults={"email": "demo@example.com"})
        UserFederationLink.objects.get_or_create(user=user, federated_user=federated_identity)

        # 🪪 Issue Token
        payload = {"role": "demo"}
        token = issuer.issue_token(federated_identity, payload)

        self.stdout.write(self.style.SUCCESS("Seeded identity models and issued token"))
        self.stdout.write(f"Token: {token}")
