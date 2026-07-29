"""What workflows meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="workflows.run",
    label="Workflow run",
    app_label="workflows",
    unit="request",
    description=(
        "One DAG execution. A run does whatever its nodes say, including "
        "lambda nodes that execute code on a worker."
    ),
    default_limit=50,
))
