from oya.models import Platform
from django.http import Http404
from django.urls import reverse
from oya.serializers import PlatformSerializer


class PageProcessor:
    """
    Processes global context data for views.
    Injects platform configuration, theme, font, user info, and shared metadata.
    """

    def __init__(self):
        self.config = self._get_config()

    def get_header_data(self, request):
        nav_links = []

        # Home
        nav_links.append({
            "name": "Home",
            "url": reverse("home"),
            "icon": "fas fa-home"
        })

        # Dashboard
        nav_links.append({
            "name": "Dashboard",
            "url": reverse("dashboard"),
            "icon": "fas fa-tachometer-alt"
        })

        if request.user.is_authenticated:
            # Logout
            nav_links.append({
                "name": "Logout",
                "url": reverse("parent_logout"),
                "icon": "fas fa-sign-out-alt"
            })
        else:
            # Login
            nav_links.append({
                "name": "Login",
                "url": reverse("parent_login"),
                "icon": "fas fa-sign-in-alt"
            })

        return {
            "nav_links": nav_links,
            "user_logged_in": request.user.is_authenticated,
        }

    def _get_config(self):
        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise Http404("No active platform configuration found.")
        return platform

    def decorate(self, context, request):
        platform = PlatformSerializer(self.config).data
        context.update({
            "platform": platform,
            "font": platform["theme"].get("font", {}),
            "theme": platform["theme"],
            "user": request.user,
            "is_authenticated": request.user.is_authenticated,
            "header": self.get_header_data(request)
        })
        return context
