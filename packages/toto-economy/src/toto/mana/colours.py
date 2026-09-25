"""Which pool each metered action draws on. Pure data — no Django imports.

Three colours, one per family of cost a member can reason about:

* **security** (``security``, cyan) — screening, and plaintext left lying
  about. Encrypting a file earns it back.
* **compute** (``warn``, red) — runs, renders, sends, tokens, time holds.
* **storage** (``success``, green) — bytes in, bytes out, bytes held.

WHY A DICT HERE AND NOT ``Metric.metadata``. Twelve apps in four wheels
register metrics, and ``toto.quota`` is deliberately ignorant of money
(`quota/metrics.py` — "no prices here"). Putting the colour on each metric
would spread a mana fact across every wheel and teach the registry about
currencies. One table, in the app that owns the concept, keeps a new metric to
one edit — and the "is anything unmapped" audit to a set difference, pinned by
``tests/test_colours.py`` so a metric nobody coloured fails CI rather than
running unpriced.
"""

from __future__ import annotations

from decimal import Decimal

ROLES = ("security", "compute", "storage")

#: How each pool reads, independent of the theme's actual hex values.
#: The ticker stays BLUE: it is the ledger unit and the mint reference, and
#: renaming it would not be backward compatible. Only the rendering is cyan.
HUE = {"security": "cyan", "compute": "red", "storage": "green"}

#: The asset each pool is denominated in, created by ``toto.mana.bootstrap``.
TICKER = {"security": "BLUE", "compute": "RED", "storage": "GREEN"}

COLOUR_OF: dict[str, str] = {
    # -- storage ------------------------------------------------------------
    "storage.request": "storage",
    "storage.transfer_mb": "storage",
    # Mapped, deliberately NOT priced: egress is cap-only by doctrine until
    # who-pays-for-anonymous-downloads is decided (economy.md).
    "storage.egress_mb": "storage",
    "storage.gb_day": "storage",                # a levy, clamped
    # -- compute ------------------------------------------------------------
    "workflows.run": "compute",
    "repo.run": "compute",
    "repo.op": "compute",
    "memo.pdf": "compute",
    # Back on zenobia 2026-09-25: one render on the pdf worker, billed only
    # when the PDF is filed.
    "aralia.render": "compute",
    "memo.save": "compute",
    "cyprian.save": "compute",
    # Sheets are back on zenobia since 2026-09-25: one deliberate save.
    "primula.save": "compute",
    "time.hold": "compute",
    "jess.send": "compute",
    "mail.send": "compute",
    "assets.chain.verify": "compute",
    "ai.request": "compute",
    "ai.tokens_1k": "compute",
    # -- security -----------------------------------------------------------
    "antivirus.scan": "security",
    "security.plain_gb_day": "security",        # a levy, clamped
}

#: Registered somewhere, and deliberately NOT mana. Named so the audit can tell
#: "decided" apart from "forgotten". A subscription is bought in the settlement
#: currency, not drawn from a pool; the rest belong to parked or uninstalled
#: apps whose metrics still register on hosts that install them.
NOT_MANA: frozenset[str] = frozenset({
    "subscription.month",
    "anastasia.execution", "dracena.execute", "texlab.compile",
    "fileservices.run", "manta.job", "ocr.page", "sketch.save",
    "polls.pdf",
})

#: Seed prices, in display units of the pool's own asset. A pool holds 100 and
#: refills 4 an hour, so these read as "how much of a day's refill".
#: Placeholders to be tuned; staff own the number after seeding, and
#: ``settings.MANA_PRICES`` overrides per host (``None`` removes one).
PRICES: dict[str, Decimal] = {
    "storage.request": Decimal("0.5"),
    "storage.transfer_mb": Decimal("0.05"),
    "storage.gb_day": Decimal("2"),
    "workflows.run": Decimal("2"),
    "repo.run": Decimal("2"),
    "repo.op": Decimal("0.5"),
    "memo.pdf": Decimal("5"),
    "aralia.render": Decimal("5"),
    "time.hold": Decimal("1"),
    "jess.send": Decimal("1"),
    "mail.send": Decimal("1"),
    "assets.chain.verify": Decimal("1"),
    "ai.request": Decimal("2"),
    "ai.tokens_1k": Decimal("0.5"),
    "antivirus.scan": Decimal("3"),
    "security.plain_gb_day": Decimal("20"),
}

#: (regen per hour, pool maximum), per role. Empty to full in about a day.
REGEN_DEFAULTS: dict[str, tuple[Decimal, Decimal]] = {
    role: (Decimal("4"), Decimal("100")) for role in ROLES
}

#: Encrypting a vault file: what it earns, and how often. Morion's rekey
#: reward, which is the same shape of act — once per file per UTC day, at most
#: ``ENCRYPT_DAILY_CAP`` MANA (an amount, not a count) earned this way a day,
#: and never more than the room left in the pool.
ENCRYPT_REWARD = Decimal("10")
ENCRYPT_DAILY_CAP = Decimal("30")
