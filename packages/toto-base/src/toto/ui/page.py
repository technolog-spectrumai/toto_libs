from django.conf import settings
from django.http import Http404
from django.templatetags.static import static
from django.urls import NoReverseMatch, reverse

from toto.core.models import Platform
from toto.core.serializers import PlatformSerializer

#: Theme fonts the platform serves itself, by the Font row's name: the woff2
#: file under this library's static/ and the weights it covers (2026-10-01,
#: 37c.20). With USE_EXTERNAL_FONTS off, oya/base.html draws an @font-face
#: from here instead of linking the font's stylesheet: every seeded font is a
#: Google Fonts link, and Google then learns each visitor's address and
#: browser before any page has said a word. Orbitron is the seeded theme's
#: ("Amazing Moon"): the very file Google served (fonts.gstatic.com v35, the
#: latin subset — Orbitron has no other — one variable font for 400 to 900),
#: beside its SIL Open Font License. A font with no entry falls back to its
#: style family.
LOCAL_FONTS = {
    "Orbitron": {"file": "oya/fonts/orbitron/orbitron-latin.woff2", "weight": "400 900"},
}


def local_font(name):
    """The platform's own copy of a theme font, as {url, weight}, or None."""
    entry = LOCAL_FONTS.get(name or "")
    if entry is None:
        return None
    return {"url": static(entry["file"]), "weight": entry["weight"]}


class PageProcessor:
    """
    Injects platform configuration, theme, font, user info, and nav items
    into the template context for every view.
    """

    def __init__(self, maintenance_mode=False):
        self.config = None if maintenance_mode else self._get_config()

    def _resolve_nav_items(self, request):
        items = []
        for item in getattr(settings, "HEADER_NAV_ITEMS", []):
            if item.get("requires_auth") and not request.user.is_authenticated:
                continue
            url_name = item.get("url_name")
            try:
                url = reverse(url_name)
            except NoReverseMatch:
                continue
            items.append({"label": item["label"], "icon": item.get("icon", ""), "url": url})
        return items

    def _get_config(self):
        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise Http404("No active platform configuration found.")
        return platform

    def decorate(self, context, request):
        base = {
            "user": request.user,
            "is_authenticated": request.user.is_authenticated,
            "header_nav_items": self._resolve_nav_items(request),
            "use_external_fonts": getattr(settings, "USE_EXTERNAL_FONTS", True),
        }

        if self.config is None:
            context.update({**base, "platform": None, "font": {}, "theme": {},
                            "local_font": None, "logo": None, "federation": None,
                            "brand": self._brand(None, None)})
            return context

        platform_data = PlatformSerializer(self.config).data
        theme_data = platform_data.get("theme") or {}
        font = theme_data.get("font") or {}
        federation = (
            {
                "name": self.config.federation.name,
                "description": self.config.federation.description,
                # Already a URL, NOT an ImageField: a template that writes
                # `federation.logo.url` gets an empty src. Three of them did.
                "logo": (self.config.federation.logo.url
                         if self.config.federation.logo else None),
            }
            if self.config.federation_id else None
        )
        context.update({
            **base,
            "platform": platform_data,
            "font": theme_data.get("font", {}),
            # Asked only when the stylesheet link is off: one or the other.
            "local_font": (None if base["use_external_fonts"]
                           else local_font(font.get("name"))),
            "theme": theme_data,
            "logo": self.config.logo.url if self.config.logo else None,
            # The platform's federation, for hosts that brand with it (the
            # holding identity). None-safe: a platform without one is the
            # common case, and stock templates ignore the key entirely.
            "federation": federation,
            # What the chrome actually wears. Resolved HERE rather than by a
            # chain of {% if %} in every template that shows a brand — see
            # `_brand`.
            "brand": self._brand(federation, platform_data),
        })
        return context

    def _brand(self, federation, platform_data) -> dict:
        """The name, logo and description the app bar and welcome page wear.

        Resolved once, per field, so a federation that has a name but no logo
        keeps the platform's logo instead of losing it.

        **Gated on the host.** `BRAND_FROM_FEDERATION` defaults to False, so a
        host that upgrades looks exactly as it did: every install that ever
        ran `init_data` carries a seeded "Toto-Federation" attached to its
        platform, and preferring it unasked would rename somebody's app bar
        overnight. A host that wants the holding identity says so.

        `source` names the winner, which is what a test can assert on and
        what a template can use to caption the thing.
        """
        platform_data = platform_data or {}
        logo = self.config.logo.url if (
            self.config is not None and self.config.logo) else None
        fallback = {"name": platform_data.get("site_name") or "",
                    "logo": logo, "description": "", "source": "platform"}
        if not getattr(settings, "BRAND_FROM_FEDERATION", False):
            return fallback
        if not federation:
            return fallback
        return {
            "name": federation["name"] or fallback["name"],
            "logo": federation["logo"] or fallback["logo"],
            "description": federation["description"] or "",
            "source": "federation",
        }
