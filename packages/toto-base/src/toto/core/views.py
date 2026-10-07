from toto.core.models import Platform
from toto.ui import PageProcessor
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_safe
import logging
from toto.core import assistant, auth_views
from toto.core.monitoring import superuser_on_plan
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
        # The tailnet address, when this stack has one. It REPLACES the
        # browser-derived QR below: on a tailscale deploy the MagicDNS name is
        # the address to hand round, and location.origin is whatever the person
        # who ran the deploy happened to type — often localhost, which is
        # exactly the URL that will not work on anybody's phone.
        "tailnet_public_url": getattr(settings, "TAILNET_PUBLIC_URL", ""),
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


#: The words `_resolve_dashboard_item` has an arm for, besides "group:<name>".
DASHBOARD_VISIBILITIES = ("public", "private", "staff", "superuser")


def _superuser_with_plan(user) -> bool:
    """Superuser functionality needs the account AND, where the host sells an
    admin-only plan, that plan in force (toto.subscriptions, 2026-09-26)."""
    from django.apps import apps

    if not user.is_superuser:
        return False
    if not apps.is_installed("toto.subscriptions"):
        return True
    try:
        from toto.subscriptions.models import superuser_plan_active

        return superuser_plan_active(user)
    except Exception:  # noqa: BLE001 - a dashboard must render whatever breaks
        return True


def _resolve_dashboard_item(item, user):
    visibility = item.get("visibility", "public")
    authenticated = user.is_authenticated
    # An unknown word hides the tile. It used to fall through every arm below
    # and show the tile to EVERYONE: zenobia's Presentations tile said
    # "authenticated" from 2026-09-05 to 2026-09-23 and was public but for the
    # login gate. A typo in a visibility must fail closed, and say so.
    if not isinstance(visibility, str) or not (
        visibility in DASHBOARD_VISIBILITIES or visibility.startswith("group:")
    ):
        logger.warning("dashboard tile %r has unknown visibility %r; hidden",
                       item.get("title"), visibility)
        return None
    if visibility == "private" and not authenticated:
        return None
    # "superuser" cards (e.g. the Grafana "Monitoring" link) are hidden from
    # everyone but superusers. This is cosmetic — the target enforces its own
    # access — but keeps ops tools out of ordinary users' dashboards.
    if visibility == "superuser" and not (authenticated and _superuser_with_plan(user)):
        return None
    # "staff" cards (e.g. the Gitea "Code" link) show for staff AND superusers —
    # is_superuser does not imply is_staff in Django. Cosmetic like above; Gitea
    # enforces the real gate via the required `staff` role claim.
    if visibility == "staff" and not (
        authenticated and (user.is_staff or user.is_superuser)
    ):
        return None
    # "group:<name>" cards show for members of that Django group, plus
    # superusers — who administer the thing on the other side and would
    # otherwise have to add themselves to a group to see it.
    #
    # This is the access model for tools the platform hosts but does not
    # implement: a person reaches the boards because somebody put them in the
    # group, not because of what they ARE. Cosmetic here like every arm above
    # — the target enforces the real gate from the same claim, which is what
    # makes the two agree rather than merely look alike.
    if visibility.startswith("group:"):
        wanted = visibility.split(":", 1)[1].strip()
        if not authenticated:
            return None
        if not (user.is_superuser
                or user.groups.filter(name=wanted).exists()):
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


@login_required
def monitoring_view(request):
    """The one operator door: send each role to the tab it can actually open.

    Monitoring, Status and Audit were three dashboard tiles until 2026-09-01.
    They are one tile now, and one tile needs one link — but the surfaces
    behind it are gated differently: monit's three tabs are superuser-only
    (MonitAccessMixin raises 403) and audit's is staff. A single link straight
    to /monit/ would land the tile's own staff audience on a 403, which is the
    tile-matches-gate failure with one redirect added.

    So the tile points HERE and this decides. The gate below is the dashboard's
    own reading of "staff" — is_staff OR is_superuser (see
    `_resolve_dashboard_item`) — and not `staff_member_required`, because
    Django's is_superuser does not imply is_staff and a superuser minted
    without the staff bit must not be refused their own monitoring page.

    Since 2026-10-01 monit asks for the Superuser plan as well
    (`core.monitoring.superuser_on_plan`), so a superuser who has not taken
    it goes where the staff go: Monitoring would answer them 403.
    """
    user = request.user
    if not (user.is_staff or user.is_superuser):
        raise PermissionDenied
    if superuser_on_plan(user):
        return redirect("monit:overview")
    # Staff, or a superuser without the plan: Audit is the one tab they may open.
    return redirect("audit:index")


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

    features = _manual_feature_map(request, apps)
    # What the vault's own switches leave of the storage chapter
    # (2026-10-06). A host that only stores files draws Play and Edit only
    # where it names them (VAULT_STORAGE_ONLY_OPENS), and never the picture
    # viewer; every other host draws all three, as before.
    play = edit = pictures = features["vault"]
    if features["vault"]:
        from toto.vault.models import storage_only, storage_only_opens  # noqa: PLC0415

        play, edit = storage_only_opens("play"), storage_only_opens("edit")
        pictures = not storage_only()
    features.update(vault_play=play, vault_edit=edit, image_viewer=pictures)
    features["morion"] = features["morion"] and (play or edit)
    # Whether the storage chapter may promise Play / Edit buttons at all: a
    # host that only stores files installs none of these (or names neither
    # button), and its chapter says a file comes back as a download instead.
    features["viewers"] = (play or edit) and any(
        features[name] for name in (
            "vod", "markdown", "memo", "notarius", "editor", "sketch",
            "notebooks", "latex", "morion"))
    return features


def _manual_feature_map(request, apps):
    return {
        "vault": apps.is_installed("toto.vault"),
        # The recurring storage fee (levy engine). Implies the economy: toto.tax
        # ships in toto-economy, so the section may link tariffs/assets URLs.
        "tax": apps.is_installed("toto.tax"),
        # Where the three mana pools are installed, storage is paid in mana and
        # the storage-fee chapter says so instead of describing debt. Mounted,
        # not merely installed: that chapter links mana:index.
        "mana": _mounted("mana:index"),
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
        # Markdown Play is a host app's page (zenobia's toto.htmlview, which
        # replaced its wiki on 2026-10-02): mounted, so a host without it
        # documents no Play button it does not draw.
        "markdown": _mounted("htmlview:index"),
        "memo": apps.is_installed("toto.memo"),
        "notarius": apps.is_installed("toto.notarius"),
        "editor": apps.is_installed("toto.editor"),
        # Mounted: "file scans" among the metered work is the on-demand scan
        # of the antivirus desk, which a host without the app does not sell.
        "antivirus": _mounted("antivirus:index"),
        "sketch": apps.is_installed("toto.sketch"),
        # The editor a host serves behind the vault's own Play and Edit
        # buttons (zenobia's toto.morion, 2026-10-06): a host app's page, so
        # mounted — a host without it documents no button it does not draw.
        # `_manual_features` adds the vault's switch to it.
        "morion": _mounted("morion:open"),
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
        "steven": assistant.installed(),
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
# Tools — everything this host can DO to a file. The tab table lives in
# toto/core/tools.py; this view is the door. GET only, on purpose: see that
# module's header for why a hub-owned write route would be a paywall bypass.
# (Office — the sibling hub over the things people MAKE — was retired to limbo
# on 2026-09-02; its two views and their module half went with it.)
# ---------------------------------------------------------------------------


@login_required
@require_safe
def tools_view(request):
    """The Tools hub: everything this host can DO to a file, in one place.

    Tools were tabs in Office's strip until 2026-09-01, and Office itself was
    retired the day after. The placement argument stands on its own: turning
    HTML into a PDF is not an act on a document you keep anywhere, it is a
    tool you bring your own input to — so the tools kept their room when the
    hub over kept-things died.

    One view over a list of dataclasses, each resolved lazily and dropped when
    its app is unmounted. A GET and nothing else. Every tool keeps its own
    writes in its own app.
    """
    from django.shortcuts import redirect

    from toto.core import tools as tools_hub

    tools = tools_hub.available_tools()
    if not tools:
        # Nothing installed that Tools could offer. The dashboard is a better
        # answer than an empty room.
        return redirect("core:dashboard")

    context = {
        "page_title": "Tools",
        "tools": tools,
        # The same strip every tool page renders, so the tab you clicked stays
        # lit and the set stays navigable. `active` is empty here: the hub is
        # not one of the tools.
        "sections": tools_hub.tools_tabs(),
    }
    return render(request, _get_template("tools.html"),
                  PageProcessor().decorate(context, request))
