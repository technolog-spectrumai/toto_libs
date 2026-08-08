"""Recurring resource levies — what can be taxed, declared once.

The tariffs pipeline charges for *actions*: a request arrives, a price is
checked, the work is paid for before it runs. A capacity levy charges for
*state* — bytes held, kernels kept warm — capacity × time, sampled by a clock
with nobody watching. The engine that runs that clock, ``toto.tax``, ships in
``toto-economy``, which most hosts do not pin. What every host does ship is
the resource being measured, so the split mirrors :mod:`toto.quota.metrics`:
the provider contract lives here in the library, each owning app declares its
provider in ``<app>/taxes.py``, and the economy-side engine autodiscovers
those modules on the hosts where it is installed. On every other host no
``taxes.py`` is ever imported and this module is inert.

A provider answers three questions and nothing else:

* how much of the resource each user holds right now (:meth:`~LevyProvider.sample`
  / :meth:`~LevyProvider.measure`), in the resource's own raw integer unit —
  bytes for storage;
* how many raw units make one billing unit (``raw_per_unit`` — ``2**30`` for
  a GB-day metric);
* how to shed one user's holdings down to a target when the platform enforces
  (:meth:`~LevyProvider.enforce`).

No prices, no allowances, no arrears — those live on the engine's side of the
boundary. Like the metric registry, registration must stay pure: a provider
module is imported at app-registry time and may not touch the database until
one of its methods is called.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator


class DuplicateLevyProvider(Exception):
    """Two apps claimed the same levy metric code."""


@dataclass
class EnforcementResult:
    """What one enforcement run actually did, as plain data.

    ``reached_target`` is False when the provider ran out of things it was
    allowed to shed while the user was still over target — the engine records
    that as a partial enforcement rather than retrying forever.
    """

    deleted_count: int = 0
    deleted_raw: int = 0
    skipped_count: int = 0
    final_raw: int = 0
    reached_target: bool = False


class LevyProvider:
    """One taxable resource. Subclass per resource, register at import time.

    ``code`` names the provider ("vault.storage"); ``metric_code`` names the
    quota metric its billing flows through ("storage.gb_day") — the same string
    the rate card prices and the idempotency key carries. ``raw_per_unit`` is
    the integer number of raw units in one billing unit.
    """

    code: str = ""
    metric_code: str = ""
    raw_per_unit: int = 1
    #: What enforcement means for THIS resource, for the warning notice —
    #: e.g. "your raised time limits will be reset to their free defaults".
    #: Empty means the engine's default (permanent random deletion) wording.
    consequence_text: str = ""

    def format_raw(self, raw: int) -> str | None:
        """A human rendering of a raw amount ("2.5 h"), or None to let the
        UI fall back to its default (bytes formatting, historically)."""
        return None

    def sample(self) -> Iterator[tuple[int, int]]:
        """Yield ``(user_id, raw_amount)`` for every user holding any of the
        resource. One pass, as few queries as the resource allows — the daily
        levy walks this for the whole host."""
        raise NotImplementedError

    def measure(self, user) -> int:
        """This one user's current holdings in raw units, for live display."""
        raise NotImplementedError

    def enforce(
        self,
        user,
        target_raw: int,
        *,
        rng=None,
        on_deleted: Callable[[dict], None] | None = None,
        on_skipped: Callable[[dict], None] | None = None,
    ) -> EnforcementResult:
        """Permanently shed the user's holdings down to ``target_raw``.

        Selection is the provider's business (storage sheds random files); the
        contract is only that shedding is permanent, that each shed item is
        reported through ``on_deleted`` (and each refusal through
        ``on_skipped``) with a plain-data dict, and that the walk stops as
        soon as holdings are at or under target. ``rng`` accepts a seeded
        ``random.Random`` so tests can pin the selection.
        """
        raise NotImplementedError


class LevyRegistry:
    """In-memory, populated at startup. Never touches the database."""

    def __init__(self) -> None:
        self._providers: dict[str, LevyProvider] = {}

    def register(self, provider: LevyProvider) -> LevyProvider:
        """Add a provider. Raises on a duplicate metric code.

        Raising rather than overwriting is deliberate, for the same reason the
        metric registry does: two providers disagreeing about what a code
        measures would surface much later as a levy on the wrong resource.
        """
        existing = self._providers.get(provider.metric_code)
        if existing is not None and existing is not provider:
            raise DuplicateLevyProvider(
                f"{provider.metric_code!r} is already provided by {existing.code!r}; "
                f"{provider.code!r} cannot claim it too."
            )
        self._providers[provider.metric_code] = provider
        return provider

    def get(self, metric_code: str) -> LevyProvider | None:
        return self._providers.get(metric_code)

    def all(self) -> list[LevyProvider]:
        return sorted(self._providers.values(), key=lambda p: p.metric_code)

    def __len__(self) -> int:
        return len(self._providers)

    def __iter__(self) -> Iterator[LevyProvider]:
        return iter(self.all())

    def __repr__(self) -> str:
        return f"LevyRegistry({len(self._providers)} providers)"


#: The singleton every ``<app>/taxes.py`` registers into.
registry = LevyRegistry()
