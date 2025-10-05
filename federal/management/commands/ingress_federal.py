from django.contrib.auth.models import User
from oya.ingress import IngressCommand
from django.utils.crypto import get_random_string
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from federal.models import (
    AuthRSAKeyPair,
    LocalIdentityProvider,
    FederatedIdentity,
    UserFederationLink,
    Federation
)
from federal.token import TokenService


class Command(IngressCommand):
    help = "Seed identity models with RSA keys, federation, local provider, federated identity, and token"

    def process(self, _):
        self.create_dashboard_item(
            title="Federation",
            icon="sitemap",
            description="Manage federated identities.",
            link="/federal/"
        )

        # 🏛️ Create Federation
        federation = Federation.objects.create(
            name="Demo Federation",
            description="Federation for demo purposes",
            active=True
        )

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

        rsa_key = AuthRSAKeyPair.objects.create(
            key_id=get_random_string(12),
            public_key_pem=public_pem,
            private_key_pem=private_pem,
            issuer="https://your-app.example.com",
            active=True
        )

        # 🏠 Create Local Identity Provider linked to Federation
        provider = LocalIdentityProvider.objects.create(
            name="Local Federation",
            issuer_url="https://your-app.example.com",
            audience="your-app-client-id",
            rsa_key=rsa_key,
            contact_email="admin@your-app.example.com",
            metadata_url="https://your-app.example.com/.well-known/openid-configuration",
            active=True,
            token_lifetime=600,
            federation=federation
        )

        # 👤 Create Federated Identity linked to Federation
        federated_identity = FederatedIdentity.objects.create(
            subject=get_random_string(16),
            issuer=provider.issuer_url,
            email="demo@example.com",
            name="Demo User",
            federation=federation
        )

        # 🔗 Link to Local User
        user, _ = User.objects.get_or_create(username="demo_user", defaults={"email": "demo@example.com"})
        UserFederationLink.objects.get_or_create(user=user, federated_user=federated_identity)

        # 🪪 Issue Token
        token = TokenService(provider).issue_token(federated_identity)

        self.stdout.write(self.style.SUCCESS("✅ Seeded federation, identity models, and issued token"))
        self.stdout.write(f"🔐 Token:\n{token.value}")
