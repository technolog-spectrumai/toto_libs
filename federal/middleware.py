# federal/auth/middleware.py
from django.utils.deprecation import MiddlewareMixin
from django.utils.crypto import get_random_string
from django.contrib.auth import login
from federal.auth.backends import FederalTokenBackend
from federal.models import FederatedIdentity, UserFederationLink, LocalIdentityProvider
from federal.token import TokenService

class FederalAuthMiddleware(MiddlewareMixin):
    def process_request(self, request):
        # 🔐 Step 1: Token-based authentication
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            user = FederalTokenBackend().authenticate(request, token=token)
            if user:
                request.user = user
                login(request, user)

        # 🔑 Step 2: Federated identity + token issuance
        user = getattr(request, "user", None)
        if user and user.is_authenticated and "access_token" not in request.session:
            link = user.federated_links.filter(active=True).first()
            if not link:
                federated_identity = FederatedIdentity.objects.create(
                    subject=get_random_string(16),
                    issuer="https://your-app.example.com",
                    email=user.email,
                    name=user.get_full_name() or user.username
                )
                link = UserFederationLink.objects.create(user=user, federated_user=federated_identity)

            provider = LocalIdentityProvider.objects.filter(active=True).first()
            if provider:
                token = TokenService(provider).issue_token(link.federated_user)
                request.session["access_token"] = token.value
