"""What workflows meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="workflows.run",
    label=_("Workflow run"),
    app_label="workflows",
    unit="request",
    description=_(
        "One DAG execution. A run does whatever its nodes say, including "
        "lambda nodes that execute code on a worker."
    ),
    default_limit=50,
))

# How much a run works (2026-10-12; the owner: a run's cost "must depend on
# how much resource it is using and must be approximated"): the megabytes it
# processes, counted where the run is started, from what is known then (the
# file to encrypt, the files to zip). An approximation: a 1 GB file costs a
# thousand times a 1 MB one, which is the point; nothing is measured while
# the worker runs. Priced where a host prices it (Discipline); no default
# limit, so no policy row is seeded.
registry.register(Metric(
    code="workflows.compute_mb",
    label=_("Compute, megabytes processed"),
    app_label="workflows",
    unit="mb",
    description=_(
        "Megabytes a run processes, as estimated when it is started: the size of the "
        "file it encrypts or decrypts, the sizes of the files it zips. The run is "
        "refused before it starts when this is not covered."
    ),
))

