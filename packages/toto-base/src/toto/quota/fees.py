"""Where the platform's money comes from — declared once, per app.

The sibling of :mod:`toto.quota.levy`, and the same shape for the same reason.
A levy provider says "here is a resource that can be taxed"; a fee source says
"here is a way this platform earns, and here is the account it lands in".

Four sources exist today and they are stored three different ways: metered usage
writes ``UsageCharge`` rows, the capacity levies bill through that same
pipeline,
tribute writes ``TributeCharge``, and the exchange commission writes **nothing at
all** — it exists only as ledger entries. Two of the four also live in zenobia's
own portion of the tree, which toto-economy must never import.

So a source declares no amounts. It declares WHERE its money lands, and the
Fees view sums the ledger credits into that account. The ledger is the only
thing all four have in common, and it means adding a fifth source is one small
file with no aggregation code in it. The registry supplies meaning; the ledger
supplies money.

Like the levy contract this lives in toto-base, not in toto-economy, because a
host app has to be able to implement it — ``zenobia/toto/bourse/fees.py`` and
``zenobia/toto/portfolio/fees.py`` do. Registration must stay pure: a provider
module is imported at app-registry time and may not touch the database until
something asks it a question.

Only plain data crosses this boundary — the rule :mod:`toto.quota.rates` states
and this module keeps: no model instance is ever returned from a source.
"""

from __future__ import annotations

from typing import Iterator


class DuplicateFeeSource(Exception):
    """Two apps claimed the same fee source code."""


class FeeSource:
    """One way the platform earns. Subclass per source, register at import time.

    ``code`` names the source ("tariffs.usage"). ``account_code`` is the
    ``LedgerAccount.code`` its money lands in — that string is the join between
    this declaration and the ledger, and it is the only field the income figures
    are computed from.

    ``settings_url`` is a url *name* rather than a URL, resolved lazily by the
    view so that a source whose app is installed but unmounted degrades to a card
    with no link instead of a 500. Empty means there is nothing to configure.
    """

    code: str = ""
    account_code: str = ""
    #: Shown as the slice/bar label. Wrap in gettext_lazy at declaration time.
    label: str = ""
    #: One sentence: what this charges for, in a human's words.
    description: str = ""
    #: e.g. "tariffs:tariff_list". Resolved with reverse(), failures swallowed.
    settings_url: str = ""
    #: Font Awesome class for the source card.
    icon: str = "fa-solid fa-receipt"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FeeSource {self.code} → {self.account_code}>"


class FeeRegistry:
    """In-memory, populated at startup. Never touches the database."""

    def __init__(self) -> None:
        self._sources: dict[str, FeeSource] = {}

    def register(self, source: FeeSource) -> FeeSource:
        """Add a source. Raises on a duplicate code.

        Raising rather than overwriting, for the reason the levy registry gives:
        two sources disagreeing about what a code means would surface much later
        as income attributed to the wrong place — and an income chart that is
        quietly wrong is worse than one that is missing.
        """
        existing = self._sources.get(source.code)
        if existing is not None and existing is not source:
            raise DuplicateFeeSource(
                f"{source.code!r} is already registered by "
                f"{type(existing).__name__}; {type(source).__name__} cannot "
                "claim it too."
            )
        if not source.account_code:
            raise DuplicateFeeSource(
                f"{source.code!r} names no account_code, so nothing could ever "
                "be attributed to it."
            )
        self._sources[source.code] = source
        return source

    def get(self, code: str) -> FeeSource | None:
        return self._sources.get(code)

    def all(self) -> list[FeeSource]:
        return sorted(self._sources.values(), key=lambda s: s.code)

    def account_codes(self) -> list[str]:
        """Every ledger account the platform earns into, for one filtered query."""
        return [s.account_code for s in self.all()]

    def __len__(self) -> int:
        return len(self._sources)

    def __iter__(self) -> Iterator[FeeSource]:
        return iter(self.all())

    def __repr__(self) -> str:
        return f"FeeRegistry({len(self._sources)} sources)"


#: The singleton every ``<app>/fees.py`` registers into.
registry = FeeRegistry()
