"""What a plan can unlock — declared once, read everywhere.

The same registry shape as :class:`toto.quota.metrics.Metric`,
:class:`toto.quota.fees.FeeSource` and :class:`toto.quota.levy.LevyProvider`,
for the same reason: an entitlement is named in three places that must not
drift — the plan that sells it, the gate that enforces it, and the sentence on
the plans page that tells somebody what they are buying. One declaration, and
the page renders itself from the same rows the gate reads.

**The code is an app label.** ``"cyprian"``, not ``"documents"`` — because the
gate resolves ``request.resolver_match.app_name`` and nothing else, and a
mapping table between two naming schemes is a thing to keep in step.

Delta's app expresses the same idea as ``plan.code.startswith("academy")``. That
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
    """Two declarations claimed the same code."""


@dataclass(frozen=True)
class Entitlement:
    """One thing a plan can unlock, described in the words a buyer reads."""

    #: The Django ``app_name`` this gates. This IS the identity.
    code: str
    label: str
    #: One sentence, in the second person, about what you get.
    description: str = ""
    icon: str = "fa-solid fa-cube"
    #: True when nobody has to pay for it. A free entitlement is never gated,
    #: never shown as locked, and appears on every plan card.
    free: bool = False
    #: Ordering on the plan card. Lower first.
    order: int = 100


class EntitlementRegistry:
    def __init__(self):
        self._items: dict[str, Entitlement] = {}

    def register(self, entitlement: Entitlement) -> Entitlement:
        existing = self._items.get(entitlement.code)
        if existing is not None and existing != entitlement:
            raise DuplicateEntitlement(
                f"{entitlement.code!r} is already declared by another app.")
        self._items[entitlement.code] = entitlement
        return entitlement

    def get(self, code: str) -> Entitlement | None:
        return self._items.get(code)

    def all(self) -> Iterator[Entitlement]:
        return iter(sorted(self._items.values(), key=lambda e: (e.order, e.label)))

    def free_codes(self) -> set[str]:
        return {e.code for e in self._items.values() if e.free}

    def installed(self) -> Iterator[Entitlement]:
        """Only entitlements whose app is actually on this host.

        A plan that lists a document editor on a host with no document editor is
        selling nothing, and the buyer is the last person who should discover
        that.
        """
        from django.apps import apps

        for entitlement in self.all():
            if apps.is_installed(f"toto.{entitlement.code}"):
                yield entitlement


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
    Entitlement("forum", "Forum", free=True, order=5,
                icon="fa-solid fa-comments",
                description="Channels and direct messages."),
    # -- free: the economy, so you can always pay ---------------------------
    Entitlement("assets", "Wallet", free=True, order=6,
                icon="fa-solid fa-wallet",
                description="Your balance, and every transfer in and out of it."),
    Entitlement("quota", "Usage and fees", free=True, order=7,
                icon="fa-solid fa-gauge-high",
                description="What you have used, what it cost and what your limits are."),
    Entitlement("bourse", "Exchange", free=True, order=8,
                icon="fa-solid fa-chart-line",
                description="Trade one asset for another."),
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
    Entitlement("editor", "Code and text", order=24,
                icon="fa-solid fa-code",
                description="Edit text, JSON, YAML, XML, CSV, HTML and LaTeX."),
    Entitlement("kanban", "Tasks", order=25,
                icon="fa-solid fa-list-check",
                description="Boards, missions and sprints."),
    Entitlement("polls", "Polls", order=26,
                icon="fa-solid fa-square-poll-vertical",
                description="Ask a question and count the answers."),
    Entitlement("locations", "Maps", order=27,
                icon="fa-solid fa-map-location-dot",
                description="Addresses, territories and what is on them."),

    # -- the heavy end ------------------------------------------------------
    Entitlement("aralia", "Invoices", order=40,
                icon="fa-solid fa-file-invoice",
                description="Generate PDFs from a template and your own data."),
    Entitlement("mandragora", "Notebooks", order=42,
                icon="fa-solid fa-flask",
                description="Compute kernels and notebooks."),
    Entitlement("notarius", "Signatures", order=43,
                icon="fa-solid fa-signature",
                description="Sign delivered PDFs, and verify a signature later."),
    Entitlement("repo", "Version control", order=44,
                icon="fa-solid fa-code-branch",
                description="Git over your vault directories."),
    Entitlement("gitea", "Git hosting", order=45,
                icon="fa-brands fa-git-alt",
                description="Repositories hosted on this platform."),
    Entitlement("vod", "Media", order=46,
                icon="fa-solid fa-film",
                description="Video and audio, transcoded and streamed."),
    Entitlement("steven", "Assistant", order=47,
                icon="fa-solid fa-wand-magic-sparkles",
                description="Rewrite, translate and explain what you have selected."),
    Entitlement("travels", "Travels", order=49,
                icon="fa-solid fa-route",
                description="Routes, journeys and who went where."),
)

for _entitlement in _DEFAULTS:
    registry.register(_entitlement)
