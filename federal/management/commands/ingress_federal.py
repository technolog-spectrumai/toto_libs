from django.contrib.auth.models import User
from oya.ingress import IngressCommand
from django.utils.crypto import get_random_string
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from federal.models import (
    RSAKey, TrustedIssuer,
    FederatedIdentity, UserFederationLink,
    IdentityToken, AccessToken
)


class Command(IngressCommand):
    help = "Seed identity models with RSA keys, trusted issuers, federated identities, links, and signed tokens"

    def process(self, _):
        # self.create_dashboard_item(
        #     title="Seeded Identity",
        #     icon="shield",
        #     description="RSA keys, federated identities, and signed tokens for demo purposes.",
        #     link="/admin/identity/"
        # )

        self.stdout.write("🔐 Starting identity ingress...")

        # Ensure a user exists
        user = User.objects.first()
        if not user:
            user = User.objects.create_user(username="demo_user", email="demo@example.com", password="demo123")
            self.stdout.write("👤 Created demo user.")

        # 🔑 Generate RSA key pair
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ).decode()

        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

        rsa_key = RSAKey.objects.create(
            key_id=get_random_string(10),
            public_key_pem=public_pem,
            private_key_pem=private_pem,
            issuer="https://seeded-issuer.example.com",
            active=True
        )
        self.stdout.write("🔑 RSA key created.")

        # 🌐 Trusted Issuer
        issuer = TrustedIssuer.objects.create(
            name="Seeded Issuer",
            issuer_url=rsa_key.issuer,
            jwks_url=f"{rsa_key.issuer}/.well-known/jwks.json",
            audience="demo-audience",
            trusted=True
        )
        self.stdout.write("🌐 Trusted issuer created.")

        # 👤 Federated Identity
        federated = FederatedIdentity.objects.create(
            subject=get_random_string(16),
            issuer=issuer.issuer_url,
            email="federated@example.com",
            name="Federated User"
        )
        self.stdout.write("👤 Federated identity created.")

        # 🔗 Link to local user
        UserFederationLink.objects.create(
            user=user,
            federated_user=federated,
            active=True
        )
        self.stdout.write("🔗 User federation link created.")

        # 🪪 Identity Token
        id_token = IdentityToken.objects.create(
            user=user,
            audience=issuer.audience,
            issuer=rsa_key.issuer,
            expires_at=datetime.utcnow() + timedelta(minutes=5)
        )
        id_token.sign_token(rsa_key, payload={"role": "user"})
        id_token.save()
        self.stdout.write("🪪 Identity token created.")

        # 🔐 Access Token
        access_token = AccessToken.objects.create(
            user=user,
            audience=issuer.audience,
            issuer=rsa_key.issuer,
            expires_at=datetime.utcnow() + timedelta(minutes=10)
        )
        access_token.sign_token(rsa_key, payload={"scope": "read write"})
        access_token.save()
        self.stdout.write("🔐 Access token created.")

        self.stdout.write(self.style.SUCCESS("✅ Identity ingress completed."))
