from toto.core.models import Platform
from toto.ui import PageProcessor
from django.contrib.auth import get_user_model
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_safe
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
        # NOT `platform.federation`: `decorate` overwrites this key with its
        # own dict (whose `logo` is already a URL), so putting the model here
        # only ever looked like it worked. Setting it twice is what let three
        # templates dereference `federation.logo.url` and render nothing.
        "connect_url": _connect_url(platform),
        "connect_qr_url": _connect_qr_url(),
        "tailscale_url": _tailscale_url(),
        "tailscale_qr_url": _tailscale_qr_url(),
    }

    return render(request, _get_template("home.html"), processor.decorate(context, request))


def _plan_allows(user, link) -> bool:
    """Whether this user's plan grants the app a tile opens.

    Cosmetic, like the visibility arms below — the subscription gate
    middleware enforces the real 402 — but a dashboard that advertises tiles
    the plan then refuses is a hallway of doors that slam. The tile's link
    namespace IS the entitlement code (the same ``app_name`` the gate reads
    from ``resolver_match``), so the dashboard and the middleware cannot
    disagree about what is withheld.

    Answers True unless the subscriptions app is installed AND declares the
    app AND the plan withholds it — a host without plans, an undeclared app,
    or any error keeps every tile, because hiding functionality by accident
    is a silent outage.
    """
    if not link or ":" not in link:
        return True
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.subscriptions"):
        return True
    try:
        from toto.subscriptions.gate import is_entitled

        return is_entitled(user, link.split(":", 1)[0])
    except Exception:  # noqa: BLE001 - a dashboard must render whatever breaks
        return True


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
    if not _plan_allows(user, item.get("link")):
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
        # Polls are a room feature now: the chapter belongs to whoever has
        # the forum, and it links into a room rather than to a separate app.
        "polls": apps.is_installed("toto.forum"),
        "vod": apps.is_installed("toto.vod"),
        "memo": apps.is_installed("toto.memo"),
        "notarius": apps.is_installed("toto.notarius"),
        "editor": apps.is_installed("toto.editor"),
        "sketch": apps.is_installed("toto.sketch"),
        "chat": apps.is_installed("toto.forum"),
        # Mounted, not installed — and the distinction is load-bearing here.
        # toto.workflows is the platform's job runner: the antivirus queues
        # scans through it and weather loads through it, so a host may not
        # uninstall it. A host may still decline to OFFER it: zenobia is a
        # company-management product and does not hand its users a DAG
        # builder, so it drops the route while keeping the runner. Asking the
        # app registry would keep documenting a page that host does not serve
        # — and the section below reverses `workflows:workflow_list`, so it
        # would 500 the manual outright rather than merely mislead.
        "workflows": _mounted("workflows:workflow_list"),
        "notebooks": _mounted("mandragora:notebook_list"),
        # `_mounted`, not `is_installed`, and it is the last section that was
        # not. Its chapter reverses `ravioli:query_unified`, and no host in
        # this repository has a mount row for ravioli at all — so the moment
        # one installed toto-graph the manual would 500 instead of gaining a
        # chapter. Dormant today (toto-graph is unpinned), which is exactly
        # when this is cheap to fix.
        "graph": _mounted("ravioli:query_unified"),
        # The tier that runs its work in a Compute Gear. Every one of these
        # sections LINKS to the page it describes, so all of them are gated on
        # `_mounted` rather than `is_installed` — the distinction the workflows
        # comment above spells out. `ocr` and `latex` moved to _mounted with
        # them: the OCR section has reversed `ocr:home` since it was written,
        # which made it a 500 waiting for the first host that installed the app
        # without mounting it.
        "ocr": _mounted("ocr:home"),
        "latex": _mounted("texlab:lobby"),
        "anastasia": _mounted("anastasia:index"),
        # Ireneo: the read-only company dashboard. _mounted, like the rest of
        # the pages the manual describes — and its section links into it.
        "ireneo": _mounted("ireneo:overview"),
        "dracena": _mounted("dracena:lobby"),
        "manta": _mounted("manta:command_builder"),
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


# ---------------------------------------------------------------------------
# Office — the shared home for documents, decks, sheets and drawings.
# The tab table, the queries and the plugin lookups live in toto/core/office.py;
# these two views are the doors. Both GET, on purpose: see that module's header
# for why an Office-owned write route would be a paywall bypass.
# ---------------------------------------------------------------------------


def _reverse_or_blank(url_name: str) -> str:
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return ""


@login_required
@require_safe
def office_view(request, section=None):
    """One tab of Office: its list, its folder panel, its actions.

    One view for every tab rather than four near-identical ones — the tabs
    differ only by which vault file types they list, and `Section` carries that
    difference as data.
    """
    from django.core.paginator import Paginator
    from django.shortcuts import redirect

    from toto.core import office
    from toto.vault.filetree import build_file_tree

    sections = office.available_sections()
    if not sections:
        # Nothing installed that Office could show. Better the dashboard than
        # an empty room with four dead tabs.
        return redirect("core:dashboard")

    current = office.SECTIONS_BY_SLUG.get(section or "")
    if current is None or current not in sections:
        # An unknown slug, or a tab this host does not serve (BUILD_PRIMULA
        # off, say). Land on the first real tab instead of 404-ing a URL that
        # was correct on another deployment.
        return redirect("office:section", section=sections[0].slug)

    search = (request.GET.get("q") or "").strip()
    sort = request.GET.get("sort") or office.DEFAULT_SORT
    if sort not in office.SORTS:
        sort = office.DEFAULT_SORT
    try:
        directory_id = int(request.GET.get("dir") or 0) or None
    except (TypeError, ValueError):
        directory_id = None

    files = office.files_for(request.user, current, search=search, sort=sort,
                             directory_id=directory_id)
    page = Paginator(files, 30).get_page(request.GET.get("page"))

    rows = [{"file": f,
             "open_url": office.open_url(f),
             # Offered beside the name rather than instead of it: a list is for
             # finding and reading, and editing is the deliberate second act.
             "edit_url": office.edit_url(request.user, f)}
            for f in page.object_list]

    context = {
        "page_title": "Office",
        "sections": [{"slug": s.slug, "label": s.label, "icon": s.icon,
                      "url": reverse("office:section", args=[s.slug]),
                      "active": s.slug == current.slug}
                     for s in sections],
        "section": current,
        # Resolved here and dropped on NoReverseMatch, the same way the
        # dashboard treats a tile whose app is unmounted: an offer that leads
        # nowhere is worse than no offer.
        "alt_view_url": (_reverse_or_blank(current.alt_view)
                         if current.alt_view else ""),
        "rows": rows,
        "page": page,
        "search": search,
        "sort": sort,
        "sorts": [{"key": k, "label": label} for k, (label, _o) in office.SORTS.items()],
        "directory_id": directory_id,
        # The folder panel is scoped to THIS tab's types, so it shows where
        # this kind of thing lives rather than the whole vault.
        "tree": build_file_tree(request.user, file_types=current.file_types),
        # The two link prefixes the shared tree partial appends an id to.
        # Reversed here rather than written as literals: the mount point is the
        # host's to choose, and a hardcoded "/office/" would be wrong the first
        # time somebody mounts this anywhere else.
        "open_prefix": reverse("office:open") + "?file=",
        "dir_link_prefix": reverse("office:section", args=[current.slug]) + "?dir=",
        "creatable": (creatable := office.creatable_types(current, request.user)),
        # Whether anything can open this tab's types at all. Only the
        # Drawings tab can be editor-less, and its read-only note must
        # not contradict an Edit button once toto.sketch is installed.
        "type_is_editable": office.type_is_editable(current),
        # Tools sit beside the tabs, not in them: a tab is a kind of
        # file you have, and a tool is something you do to one.
        "tools": office.available_tools(),
        # The same context key primula's and memo's own listings published, so
        # the gate test that used to walk those pages can walk this one.
        "can_create": bool(creatable),
        "total": page.paginator.count,
    }
    return render(request, _get_template("office.html"),
                  PageProcessor().decorate(context, request))


@login_required
@require_safe
def office_open(request):
    """Open one file with whatever this host has for its type.

    A redirect and nothing else, so the tree rows and the list rows can share
    one href without either of them learning the plugin registries. Access is
    checked here and not left to the target: `accessible_files` decides what is
    listed, and `may_read` must decide what opens, or the two disagree.
    """
    from django.http import Http404
    from django.shortcuts import get_object_or_404, redirect

    from toto.core import office
    from toto.vault import access
    from toto.vault.models import VaultFile

    try:
        file_pk = int(request.GET.get("file") or 0)
    except (TypeError, ValueError):
        raise Http404("No such file.")
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"), pk=file_pk)
    if not access.may_read(request.user, vault_file):
        # 404 rather than 403, the same reason the vault download door gives:
        # a 403 confirms the file exists and turns this into an oracle for
        # other people's filenames.
        raise Http404("No such file.")

    url = office.open_url(vault_file)
    if not url:
        raise Http404("Nothing on this server opens that file.")
    return redirect(url)
