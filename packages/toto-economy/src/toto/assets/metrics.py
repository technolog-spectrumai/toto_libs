"""What assets meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="assets.chain.verify",
    label="Ledger chain verify",
    app_label="assets",
    unit="request",
    description=(
        "Re-hashes every posted transaction to prove the chain still holds. "
        "Costs more every day the ledger runs."
    ),
    default_limit=20,
))
