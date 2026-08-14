from toto.core.models import Platform
from toto.ui import PageProcessor
from django.contrib.auth import get_user_model
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
import logging
from toto.core import auth_views
import os
from django.conf import settings
from django.urls import reverse, NoReverseMatch
from django.utils.translation import override as translation_override


logger = logging.getLogger(__name__)
User = get_user_model()


template_dir = "oya"


def _get_template(name):
    return os.path.join(template_dir, name)


def _connect_url(platform):
    """The canonical, shareable address to encode in the welcome QR.

    Prefer the .onion (faros) so the QR works regardless of how the page is viewed;
    fall back to the configured public domain; else "" (the template uses the
    browser origin). Kept layering-safe: core never hard-depends on nomad.
    """
    from django.apps import apps  # noqa: PLC0415

    if apps.is_installed("toto.nomad"):
        try:
            from toto.nomad.service import current_onion  # noqa: PLC0415
            onion = current_onion()
            if onion:
                return f"https://{onion}.onion"
        except Exception:
            pass

    domain = (platform.domain or "").strip() if platform else ""
    if domain and domain not in ("localhost", "127.0.0.1"):
        return f"https://{domain}"
    return ""


def _connect_qr_url():
    """URL of the server-rendered connect QR (faros/nomad only), or "".

    The QR is drawn server-side because Tor Browser blocks the JS that used to
    render it client-side. Kept layering-safe: core never hard-depends on nomad.
    """
    from django.apps import apps  # noqa: PLC0415

    if not apps.is_installed("toto.nomad"):
        return ""
    try:
        from django.urls import reverse  # noqa: PLC0415

        from toto.nomad.service import current_onion  # noqa: PLC0415
        if current_onion():
            return reverse("nomad:connect_qr")
    except Exception:
        pass
    return ""


def _tailscale_url():
    """The clearnet-over-tailnet connect URL (faros bound over Tailscale), or "".

    Kept layering-safe: core never hard-depends on nomad.
    """
    from django.apps import apps  # noqa: PLC0415

    if not apps.is_installed("toto.nomad"):
        return ""
    try:
        from toto.nomad.service import tailscale_url  # noqa: PLC0415
        return tailscale_url() or ""
    except Exception:
        return ""


def _tailscale_qr_url():
    """URL of the server-rendered tailnet connect QR, or "" when not published over
    Tailscale. Kept layering-safe: core never hard-depends on nomad."""
    from django.apps import apps  # noqa: PLC0415

    if not apps.is_installed("toto.nomad"):
        return ""
    try:
        from django.urls import reverse  # noqa: PLC0415

        from toto.nomad.service import tailscale_url  # noqa: PLC0415
        if tailscale_url():
            return reverse("nomad:tailscale_qr")
    except Exception:
        pass
    return ""


def welcome_view(request):
    processor = PageProcessor()

    platform = Platform.objects.filter(active=True).first()

    context = {
        "platform": platform,
        "federation": platform.federation if platform else None,
        "connect_url": _connect_url(platform),
        "connect_qr_url": _connect_qr_url(),
        "tailscale_url": _tailscale_url(),
        "tailscale_qr_url": _tailscale_qr_url(),
    }

    return render(request, _get_template("home.html"), processor.decorate(context, request))


def _resolve_dashboard_item(item, user):
    visibility = item.get("visibility", "public")
    authenticated = user.is_authenticated
    if visibility == "private" and not authenticated:
        return None
    # "superuser" cards (e.g. the Grafana "Monitoring" link) are hidden from
    # everyone but superusers. This is cosmetic — the target enforces its own
    # access — but keeps ops tools out of ordinary users' dashboards.
    if visibility == "superuser" and not (authenticated and user.is_superuser):
        return None
    # "staff" cards (e.g. the Gitea "Code" link) show for staff AND superusers —
    # is_superuser does not imply is_staff in Django. Cosmetic like above; Gitea
    # enforces the real gate via the required `staff` role claim.
    if visibility == "staff" and not (
        authenticated and (user.is_staff or user.is_superuser)
    ):
        return None
    link = item.get("link")
    if link and ":" in link:
        try:
            link = reverse(link)
        except NoReverseMatch:
            pass
    return {
        "title": item["title"],
        "description": item["description"],
        "icon": item["icon"],
        "link": link,
        "visibility": visibility,
    }


def _resolve_all_items(user):
    items_by_key = {}
    for item in settings.DASHBOARD_ITEMS:
        resolved = _resolve_dashboard_item(item, user)
        if resolved is not None:
            with translation_override("en"):
                en_key = str(item["title"])
            items_by_key[en_key] = resolved
    return items_by_key


def dashboard_view(request):
    processor = PageProcessor()
    authenticated = request.user.is_authenticated
    items_by_key = _resolve_all_items(request.user)

    if authenticated:
        groups = []
        for category in settings.DASHBOARD_CATEGORIES:
            grouped_items = [
                items_by_key[title]
                for title in category["items"]
                if title in items_by_key
            ]
            if grouped_items:
                groups.append({"title": category["title"], "items": grouped_items})
        use_groups = True
    else:
        groups = [{"title": "", "items": list(items_by_key.values())}]
        use_groups = False

    total_items = sum(len(g["items"]) for g in groups)

    context = {
        "page_title": "Dashboard",
        "groups": groups,
        "use_groups": use_groups,
        "total_items": total_items,
    }

    context = processor.decorate(context, request)

    return render(request, _get_template("dashboard.html"), context)



#: The Office area: the productivity apps, in the order somebody meets them.
#: Each entry is (app label, url name, title, icon, description). Kept here
#: rather than in settings because it is not a per-host choice — a host either
#: installed the app or it did not, and `_mounted` answers that.
OFFICE_APPS = [
    ("toto.cyprian", "cyprian:index", "Documents", "fa-solid fa-feather-pointed",
     "Long documents with real pages, and a PDF export whose page numbers are real."),
    ("toto.memo", "memo:index", "Presentations", "fa-solid fa-person-chalkboard",
     "Slideshows you build in the browser and present full-screen."),
    ("toto.primula", "primula:index", "Sheets", "fa-solid fa-table-cells",
     "Spreadsheets, each one a file in your vault with a version kept on every save."),
    ("toto.sketch", "sketch:index", "Drawings", "fa-solid fa-pen-ruler",
     "Diagrams and sketches, and any SVG you already have."),
    ("toto.aralia", "aralia:template_list", "HTML generator", "fa-solid fa-file-invoice",
     "Fill an HTML template with YAML data and generate a PDF."),
]


def office_view(request):
    """One place for the tools you make things with.

    The five apps are spread across three wheels and a host portion, so this
    lists whichever of them this server actually installed AND mounted — the
    same two guards oya/_office_tabs.html uses, because an app can be in
    INSTALLED_APPS and serve no page at all.
    """
    from django.apps import apps as django_apps
    from django.urls import reverse

    processor = PageProcessor()
    listed = []
    for label, url_name, title, icon, description in OFFICE_APPS:
        if not django_apps.is_installed(label) or not _mounted(url_name):
            continue
        listed.append({"title": title, "icon": icon, "description": description,
                       "url": reverse(url_name)})

    context = processor.decorate({"apps": listed, "page_title": "Office"}, request)
    return render(request, _get_template("office.html"), context)


def _mounted(url_name: str) -> bool:
    """True when a URL name actually resolves on this host.

    Stronger than `apps.is_installed` for anything the manual describes as a
    *page the reader can open*. An app can be installed and still have no UI:
    zenobia keeps `toto.mandragora` in INSTALLED_APPS purely because
    `workflows.LambdaFunction` has a live FK to its ComputeKernel, but mounts it
    at no URL. Gating on the app alone documented a notebook editor that host
    does not serve.
    """
    from django.urls import NoReverseMatch, reverse  # noqa: PLC0415

    try:
        reverse(url_name)
    except NoReverseMatch:
        return False
    return True


def _manual_features(request):
    """Which manual sections to show — only features actually installed on
    this server (portal and faros install different app subsets)."""
    from django.apps import apps  # noqa: PLC0415

    return {
        "vault": apps.is_installed("toto.vault"),
        # The recurring storage fee (levy engine). Implies the economy: toto.tax
        # ships in toto-economy, so the section may link tariffs/assets URLs.
        "tax": apps.is_installed("toto.tax"),
        "subscriptions": apps.is_installed("toto.subscriptions"),
        "gervazy": apps.is_installed("toto.gervazy"),
        "socialhub": apps.is_installed("toto.socialhub"),
        "events": apps.is_installed("toto.events"),
        "kanban": apps.is_installed("toto.kanban"),
        "locations": apps.is_installed("toto.locations")
        and getattr(settings, "LOCATIONS_UI_ENABLED", True),
        "polls": apps.is_installed("toto.polls"),
        "vod": apps.is_installed("toto.vod"),
        "memo": apps.is_installed("toto.memo"),
        "notarius": apps.is_installed("toto.notarius"),
        "editor": apps.is_installed("toto.editor"),
        "sketch": apps.is_installed("toto.sketch"),
        "chat": apps.is_installed("toto.forum"),
        "workflows": apps.is_installed("toto.workflows"),
        "notebooks": _mounted("mandragora:notebook_list"),
        "graph": apps.is_installed("toto.ravioli"),
        "ocr": apps.is_installed("toto.ocr"),
        "latex": apps.is_installed("toto.texlab"),
        "steven": apps.is_installed("toto.steven"),
        # Cosmetic gate like the dashboard "Monitoring" card — Grafana enforces
        # its own superuser-only access via OIDC role mapping.
        "grafana": bool(getattr(settings, "GRAFANA_ENABLED", False))
        and request.user.is_authenticated
        and request.user.is_superuser,
        # Cosmetic gate like the dashboard "Code" card — Gitea enforces its own
        # staff-only access via the required `staff` role claim.
        "gitea": bool(getattr(settings, "GITEA_ENABLED", False))
        and request.user.is_authenticated
        and (request.user.is_staff or request.user.is_superuser),
    }


@login_required
def manual_view(request):
    from django.utils.translation import get_language  # noqa: PLC0415

    lang = (get_language() or "en").lower()
    body_template = (
        "oya/manual/_body_pl.html" if lang.startswith("pl") else "oya/manual/_body_en.html"
    )

    processor = PageProcessor()
    context = {
        "page_title": "User Manual",
        "features": _manual_features(request),
        "manual_body_template": body_template,
    }
    return render(request, _get_template("manual.html"), processor.decorate(context, request))


def not_implemented(request):
    processor = PageProcessor()
    context = {
        "page_title": "Not Implemented"
    }
    return render(request, _get_template("placeholder.html"), processor.decorate(context, request))


def maintenance_view(request):
    processor = PageProcessor(maintenance_mode=True)
    context = {"page_title": "Under Maintenance"}
    return render(request, _get_template("maintenance.html"), processor.decorate(context, request))


def login_view(request):
    return auth_views.password_login_view(
        request, template_name="oya/login.html", page_title="Login"
    )


def logout_view(request):
    return auth_views.password_logout_view(request)






