# federal/auth/backends.py
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User
from federal.models import FederatedIdentity, UserFederationLink, ExternalIdentityProvider
from federal.token import TokenService


class FederalTokenBackend(BaseBackend):
    def authenticate(self, request, token=None):
        if not token:
            return None

        # 🔍 Verify token and extract claims
        identity = TokenService.verify_token(token)
        if not identity:
            return None

        # 🧩 Match federated identity
        federated_user = FederatedIdentity.objects.filter(
            subject=identity["sub"],
            issuer=identity["iss"]
        ).first()
        if not federated_user:
            return None

        # 🔗 Find linked Django user
        link = UserFederationLink.objects.filter(federated_user=federated_user, active=True).first()
        return link.user if link else None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
