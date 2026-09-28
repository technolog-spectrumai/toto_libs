"""Charging a geocoding lookup: before, a check; after, one atomic entry.

    check_affordable(user, n)       quota cap + mana for n lookups; raises,
                                    stores nothing, calls nobody
    ... the lookup ...
    settle_lookup(user, label)      record_usage + charge, in one transaction

Each lookup is its own source (a fresh uuid): there is no row to key it on,
and two identical searches are two lookups. The ledger posting is atomic with
the usage event, so a charge the ledger refuses leaves no event behind.

Compute mana (toto/mana/colours.py). The rate is a Tariff row seeded by
`ingress_mana`, overridable with `settings.MANA_PRICES`; `{% price_hint
"locations.geocode" %}` shows it beside every control that spends it.
Imports only the quota gateway, never toto.mana or toto.tariffs, so a host
with no economy geocodes for free under the same cap.
"""

from __future__ import annotations

import uuid

from django.db import transaction

from toto.quota.api import check_quota, record_usage
from toto.quota.charge import charge, check_funds, price_for

APP = "locations"
GEOCODE = "locations.geocode"
UNIT = "lookup"


def check_affordable(user, n=1) -> None:
    """Raise QuotaExceeded / InArrears / InsufficientFunds if ``n`` lookups
    would be refused. Asked for the whole batch up front by a caller that
    resolves several names, so the second is never refused after the first
    was charged."""
    from .models import LocationsQuotaPolicy

    check_quota(LocationsQuotaPolicy, GEOCODE, n, user)
    check_funds(user, price_for(user, APP), GEOCODE, n)


def settle_lookup(user, label) -> bool:
    """Record and charge one lookup that answered.

    ``label`` names the kind of lookup and nothing else: the ledger and the
    usage tables are read by people who have no business knowing what a member
    searched for or where they pinned. Raises (and records nothing) if the
    ledger refuses."""
    from .models import LocationsUsageEvent

    source_id = str(uuid.uuid4())
    source = {"source_type": GEOCODE, "source_id": source_id}
    with transaction.atomic():
        event = record_usage(LocationsUsageEvent, GEOCODE, 1, user, unit=UNIT,
                             idempotency_key=f"{GEOCODE}:{source_id}",
                             source_label=label, **source)
        if event is None:
            return False
        charge(user, price_for(user, APP), GEOCODE, 1, unit=UNIT,
               description=label, **source)
    return True
