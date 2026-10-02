"""What a plan can unlock — declared once, read everywhere.

The same registry shape as :class:`toto.quota.metrics.Metric`,
:class:`toto.quota.fees.FeeSource` and :class:`toto.quota.levy.LevyProvider`,
for the same reason: an entitlement is named in three places that must not
drift — the plan that sells it, the gate that enforces it, and the sentence on
the plans page that tells somebody what they are buying. One declaration, and
the page renders itself from the same rows the gate reads.

**The feature key is an app label.** ``"cyprian"``, not ``"documents"`` — because the
gate resolves ``request.resolver_match.app_name`` and nothing else, and a
mapping table between two naming schemes is a thing to keep in step.

Delta's app expresses the same idea as ``plan.key.startswith("academy")``. That
is smaller and it is not enough here: it can describe one bundle, its plan page
can say nothing about what a plan contains, and adding a second bundle means a
second prefix convention nobody wrote down.

**Registration is pure data.** No model imports, no database — this module is
imported from ``SubscriptionsConfig.ready()``, which runs before ``migrate`` and
during ``collectstatic``. An app may add its own by shipping
``<app>/entitlements.py``; the defaults below cover what this suite ships today
so that turning the feature on does not require editing fifteen apps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


class DuplicateEntitlement(Exception):
    """Two declarations claimed the same feature key."""


@dataclass(frozen=True)
class Entitlement:
    """One thing a plan can unlock, described in the words a buyer reads.

    ``feature_key`` is the stable, globally unique name a plan lists. It IS the
    Django ``app_name``, deliberately: the gate resolves
    ``resolver_match.app_name`` and nothing else, so a mapping table between
    two naming schemes would be a thing to keep in step. ``code`` remains as a
    read-only alias — it was the field's name until 2026-09-02 and every
    positional construction below still reads the same.
    """

    #: The Django ``app_name`` this gates. This IS the identity.
    feature_key: str
    label: str
    #: One sentence, in the second person, about what you get.
    description: str = ""
    icon: str = "fa-solid fa-cube"
    #: True when nobody has to pay for it. A free entitlement is never gated,
    #: never shown as locked, and appears on every plan card.
    free: bool = False
    #: Ordering on the plan card. Lower first.
    order: int = 100

    @property
    def code(self) -> str:
        """The pre-2026-09-02 name for :attr:`feature_key`."""
        return self.feature_key


class EntitlementRegistry:
    def __init__(self):
        self._items: dict[str, Entitlement] = {}

    def register(self, entitlement: Entitlement) -> Entitlement:
        existing = self._items.get(entitlement.feature_key)
        if existing is not None and existing != entitlement:
            raise DuplicateEntitlement(
                f"{entitlement.feature_key!r} is already declared by another app.")
        self._items[entitlement.feature_key] = entitlement
        return entitlement

    def get(self, code: str) -> Entitlement | None:
        return self._items.get(code)

    def declared_keys(self) -> set[str]:
        """Every registered feature_key. What the plan validator checks against."""
        return set(self._items)

    def all(self) -> Iterator[Entitlement]:
        return iter(sorted(self._items.values(), key=lambda e: (e.order, e.label)))

    def free_keys(self) -> set[str]:
        return {e.feature_key for e in self._items.values() if e.free}

    #: The pre-2026-09-02 name. Kept: gate.py and host tests both call it.
    free_codes = free_keys

    def installed(self) -> Iterator[Entitlement]:
        """Only entitlements this host actually serves.

        A plan that lists a document editor on a host with no document editor
        is selling nothing, and the buyer is the last person who should
        discover that.

        TWO CONDITIONS, and the second is the one that was missing. The app has
        to be in ``INSTALLED_APPS`` **and** something has to be mounted under
        its namespace. `apps.is_installed` alone is not the same question:
        zenobia keeps ``toto.mandragora`` installed purely because
        ``workflows.LambdaFunction`` holds a live foreign key into its
        ComputeKernel, and mounts it at no URL at all — so the plans page sold
        "Notebooks" on a build with no notebooks in it. `core.views._mounted`
        learned the same lesson for the manual, in the same words.

        Asking the URL conf is not a proxy for the real question, it IS the
        real question: ``SubscriptionGateMiddleware`` reads
        ``resolver_match.app_name`` and nothing else, so a code no mounted URL
        carries gates nothing and can unlock nothing. What is sold and what is
        enforced come from one fact.
        """
        from django.apps import apps

        mounted = mounted_app_names()
        served = served_feature_keys()
        for entitlement in self.all():
            if not (apps.is_installed(f"toto.{entitlement.feature_key}")
                    or entitlement.feature_key in served):
                continue
            if entitlement.feature_key not in mounted:
                continue
            yield entitlement


def served_feature_keys() -> set:
    """Feature keys an installed app serves under another module name.

    An app says so with ``entitlement = "<key>"`` on its AppConfig. zenobia's
    deck player (``toto.decks``, 2026-10-02) sells Presentations, whose key is
    ``memo``, while ``toto.memo`` itself stays parked: the key is what plans
    list and what the gate reads off ``app_name``, so it cannot follow the
    module's name. The second condition of :meth:`EntitlementRegistry.installed`
    (something mounted under the key) still applies unchanged.
    """
    from django.apps import apps

    return {key for key in (getattr(config, "entitlement", "")
                            for config in apps.get_app_configs()) if key}


def mounted_app_names() -> set:
    """Every ``app_name`` reachable in this host's URL conf.

    Walks the whole tree rather than reading ``get_resolver().app_dict``: that
    dict holds only the resolvers directly beneath the root, and an app
    included inside a group — zenobia splices several through helper lists —
    would be missing from it and would look unsold while being perfectly
    reachable. Under-reporting here removes a feature somebody has paid for,
    so the expensive walk is the right trade.

    Deliberately uncached. The URL conf is swapped per test with
    ``override_settings(ROOT_URLCONF=…)``, and a cache keyed on anything less
    than that answers the previous test's question. The walk is a few hundred
    patterns and runs on a page render, not in a loop.

    Never raises: a malformed urlconf must not take the plans page with it.
    An empty set then means "sell nothing", which is the safe direction —
    the gate is unaffected either way, because this decides only what is
    DISPLAYED.
    """
    from django.urls import get_resolver
    from django.urls.resolvers import URLResolver

    names = set()

    def walk(resolver, depth=0):
        # A cycle in an include() would otherwise spin forever. Nothing in this
        # suite nests anywhere near this deep.
        if depth > 20:
            return
        app_name = getattr(resolver, "app_name", None)
        if app_name:
            names.add(app_name)
        for pattern in getattr(resolver, "url_patterns", ()):
            if isinstance(pattern, URLResolver):
                walk(pattern, depth + 1)

    try:
        walk(get_resolver())
    except Exception:  # noqa: BLE001 - see the docstring
        return set()
    return names


registry = EntitlementRegistry()


# ---------------------------------------------------------------------------
# The default catalogue
# ---------------------------------------------------------------------------
# Declared here rather than in fifteen `<app>/entitlements.py` files. The
# registry is still open — an app that wants to describe itself can — but a
# feature that required touching every app in the suite before it could be
# switched on would not get switched on.
#
# `free=True` is the answer to "what does somebody with no subscription get".
# Two rules decide it, and both are about being able to LEAVE:
#
#   * you keep your identity and your files. Nothing anybody stored is behind a
#     paywall, ever — that is what makes lapsing safe.
#   * you can always reach the money. A wallet you cannot open is a wallet you
#     cannot fund, and a subscription you cannot buy.

_DEFAULTS = (
    # -- free: identity, storage, the commons --------------------------------
    Entitlement("core", "The platform", free=True, order=0,
                icon="fa-solid fa-house",
                description="Your dashboard, your profile and the manual."),
    Entitlement("socialhub", "Communities", free=True, order=1,
                icon="fa-solid fa-people-group",
                description="Your communities, their news and their rosters."),
    Entitlement("people", "People", free=True, order=2,
                icon="fa-solid fa-address-card",
                description="The directory, and your own profile in it."),
    Entitlement("vault", "Your files", free=True, order=3,
                icon="fa-solid fa-box-archive",
                description="Store, share and download files. Metered by size."),
    Entitlement("events", "Events", free=True, order=4,
                icon="fa-solid fa-calendar-days",
                description="The calendar and what is on it."),
    # -- free: the economy, so you can always pay ---------------------------
    Entitlement("assets", "Wallet", free=True, order=6,
                icon="fa-solid fa-wallet",
                description="Your balance, and every transfer in and out of it."),
    Entitlement("quota", "Usage and fees", free=True, order=7,
                icon="fa-solid fa-gauge-high",
                description="What you have used, what it cost and what your limits are."),
    Entitlement("subscriptions", "Plans", free=True, order=9,
                icon="fa-solid fa-id-card",
                description="This page. Never behind the thing it sells."),

    # -- free: the machinery (8/2026) ---------------------------------------
    # Plans differ by FUNCTIONALITY, not internals: every plan gets celery,
    # the workers, and the plumbing features ride on — selling the engine
    # separately from the features would gate one thing twice. The one
    # exception is federation (SSO pairing, the clearing bridge): that is
    # operator infrastructure between HOSTS, not a member perk, and it is
    # deliberately absent from this catalogue rather than free in it.
    Entitlement("workflows", "Workflows", free=True, order=10,
                icon="fa-solid fa-diagram-project",
                description="Automate multi-step jobs and run them on a schedule."),
    Entitlement("jess", "Email", free=True, order=11,
                icon="fa-solid fa-envelope",
                description="Send mail from the platform."),

    # -- the editors --------------------------------------------------------
    Entitlement("cyprian", "Documents", order=20,
                icon="fa-solid fa-file-lines",
                description="Write documents, and export them as PDF."),
    Entitlement("memo", "Presentations", order=21,
                icon="fa-solid fa-display",
                description="Build slide decks and present them."),
    Entitlement("primula", "Sheets", order=22,
                icon="fa-solid fa-table-cells",
                description="Build spreadsheets, with formulas and formatting."),
    Entitlement("sketch", "Drawings", order=23,
                icon="fa-solid fa-pen-ruler",
                description="Draw diagrams, and edit any SVG you already have."),
    Entitlement("editor", "Code and text", order=24,
                icon="fa-solid fa-code",
                description="Edit text, JSON, YAML, XML, CSV, HTML and LaTeX."),
    Entitlement("kanban", "Tasks", order=25,
                icon="fa-solid fa-list-check",
                description="Boards, missions and sprints."),
    # No "lacedo" (Bounties): the app was parked on 2026-08-29, and so was
    # toto.hesperis before it. Removed from BOTH here and from the plans that
    # granted it in ingress_subscriptions.py, together — a code declared here
    # but granted by no plan hides its tile from everybody and answers 402 to
    # every write. Order 26 is left vacant; the numbers only need to sort.
    Entitlement("locations", "Maps", order=27,
                icon="fa-solid fa-map-location-dot",
                description="Addresses, territories and what is on them."),
    # Places (1.51): named points on the map, each with a note (the kinds and
    # the hours went on 2026-09-28), a tab inside Maps on the host that ships
    # it (zenobia's toto.places), sold with Maps.
    Entitlement("places", "Places", order=27,
                icon="fa-solid fa-building",
                description="Named points on the map, each with a note and comments."),

    # -- the heavy end ------------------------------------------------------
    Entitlement("aralia", "PDF generator", order=40,
                icon="fa-solid fa-file-pdf",
                description="Render an HTML page to a PDF."),
    Entitlement("mandragora", "Notebooks", order=42,
                icon="fa-solid fa-flask",
                description="Compute kernels and notebooks."),
    Entitlement("notarius", "Signatures", order=43,
                icon="fa-solid fa-signature",
                description="Sign delivered PDFs, and verify a signature later."),
    # -- moved out of free on 2026-09-06 ------------------------------------
    # The free tier's rule was "you keep your identity and your files, and you
    # can always reach the money". Chat and the exchange were read as part of
    # that commons; they are collaboration and trading, which is what a
    # subscription is for. Identity, files, communities, events, the wallet
    # and this page stay free — lapsing is still safe, and a lapsed member can
    # still see what they owe and pay it.
    Entitlement("forum", "Forum", order=28,
                icon="fa-solid fa-comments",
                description="Channels and direct messages."),
    Entitlement("bourse", "Exchange", order=29,
                icon="fa-solid fa-chart-line",
                description="Trade one asset for another."),

    # -- operator tooling (2026-09-06) ---------------------------------------
    # Not member features: these are how somebody RUNS the platform. They are
    # declared rather than left undeclared because an app the catalogue does
    # not know is free by default (`is_entitled`), and "free by omission" is
    # not the same statement as "sold at this tier".
    #
    # Federation branding is deliberately NOT here, and could not be: it lives
    # under the `sso` namespace, which is hard-coded ALWAYS_FREE because the
    # same namespace serves LOGIN. Gating it would paywall signing in. It
    # stays staff-only, which is the same answer the note above gives for
    # federation itself — operator infrastructure between HOSTS.
    Entitlement("monit", "Monitoring", order=50,
                icon="fa-solid fa-heart-pulse",
                description="This host's health, its trends and its record."),
    # Yamabiko, 2026-09-23: one bucket kept as an echo of another, on a
    # schedule. An operator's tool (members own only their personal bucket),
    # declared and sold beside monit so it is not free by omission. Declared
    # here and granted by the professional plan in the same edit.
    Entitlement("yamabiko", "Echoes", order=51,
                icon="fa-solid fa-clone",
                description="Keep one bucket as a copy of another, on a schedule."),
    # NO "sepulka" (the sealed whole-installation archive): declared here on
    # 2026-09-06 beside monit, gone on 2026-09-23 when the only host that
    # mounted it parked the app. It did little the deploy config's own backups
    # do not, and an archive carrying every key the host holds was a leak risk
    # for as long as one existed. Removed from BOTH here and the professional
    # plan in the same edit — this is the half W001 cannot see: a plan that
    # still names an undeclared key is `subscriptions.E001`, and the build
    # stops.
    #
    # Undeclared means free by omission again, which is harmless only because
    # nothing mounts the namespace. Reviving the app means declaring it here
    # again in the same edit that mounts it.

    Entitlement("repo", "Version control", order=44,
                icon="fa-solid fa-code-branch",
                description="Git over your vault directories."),
    # NO "gitea" here since 2026-10-01: toto.gitea declares it itself
    # (toto/gitea/entitlements.py), so it is in the catalogue exactly on the
    # hosts that install the forge's page. zenobia's forge left its stack for
    # a machine of its own (nabu) that day, and with it the plan that sold it.
    Entitlement("vod", "Media", order=46,
                icon="fa-solid fa-film",
                description="Video and audio, transcoded and streamed."),
    Entitlement("steven", "Assistant", order=47,
                icon="fa-solid fa-wand-magic-sparkles",
                description="Rewrite, translate and explain what you have selected."),
    Entitlement("travels", "Travels", order=49,
                icon="fa-solid fa-route",
                description="Routes, journeys and who went where."),

    # -- compute, and the apps that run on it (1.50) -------------------------
    # These arrived when the placidia host was dismantled: the apps came back
    # to this host, their binaries did not. Every one of them does its work in
    # a Compute Gear (see portal/anastasia.md), which is why `anastasia` is
    # declared beside them rather than treated as plumbing — unlike celery and
    # the workflows engine above, reserved compute is a real, finite thing a
    # deployment hands out, and a plan is exactly where "how much" belongs.
    #
    # Gears themselves are NOT free, and that is the one judgement here: a
    # plan that included the labs but not the capacity to run them would sell
    # four buttons that all refuse. Everything a lapsed member STORED stays
    # reachable — the workspaces, the files, the PDFs already compiled are all
    # toto.vault, which is free — so lapsing loses the ability to run new
    # work, never the work already done.
    Entitlement("anastasia", "Compute Gears", order=50,
                icon="fa-solid fa-gears",
                description="Reserve CPU, memory and scratch space, and run "
                            "heavy work inside it."),
    Entitlement("texlab", "TeX Lab", order=51,
                icon="fa-solid fa-file-lines",
                description="LaTeX workspaces that compile a whole folder to PDF."),
    Entitlement("dracena", "Python Lab", order=52,
                icon="fa-brands fa-python",
                description="Notebook-style Python in a workspace, with a "
                            "kernel that survives reconnects."),
    Entitlement("manta", "Media conversion", order=53,
                icon="fa-solid fa-film",
                description="Resize, compress, cut and convert audio and video."),
    Entitlement("fileservices", "File services", order=54,
                icon="fa-solid fa-wand-magic-sparkles",
                description="Run a conversion over a file straight from the vault."),
    # NO "ocr" (Text recognition): retired on 2026-09-06 with toto-media,
    # which zenobia no longer pins. Removed from BOTH here and from the
    # professional plan in the same edit — `subscriptions.W001` catches the
    # half that forgets, and it caught this one.
    #
    # A catalogue entry is not host-specific, so this is a judgement about the
    # SUITE: no host ships text recognition today. If one does again, the
    # entry and its plan grant come back together.
)

for _entitlement in _DEFAULTS:
    registry.register(_entitlement)
