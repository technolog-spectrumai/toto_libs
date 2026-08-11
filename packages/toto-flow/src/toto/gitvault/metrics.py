"""What gitvault meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

Two metrics, because gitvault has two shapes of work and pricing them together
would misprice both. `gitvault.run` is init/push/pull: a worktree export, a
network conversation with Gitea, and a celery worker held for the duration.
`gitvault.op` is everything that shells out to git inside the request —
commit, branch, checkout, merge, log — which is cheap per call and therefore
the one worth hammering, so it gets the larger allowance and the tighter watch.
"""

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="gitvault.run",
    label="Git run",
    app_label="gitvault",
    unit="request",
    description="One init, push or pull — a worktree export plus a network git.",
    default_limit=100,
))

registry.register(Metric(
    code="gitvault.op",
    label="Git operation",
    app_label="gitvault",
    unit="request",
    description="One git subprocess run inside the request: commit, branch, merge, log.",
    default_limit=300,
))
