"""What toto.repo meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

Two metrics, because it has two shapes of work and pricing them together
would misprice both. `repo.run` is init/push/pull: a worktree export, a
network conversation with the remote, and a celery worker held for the duration.
`repo.op` is everything that shells out to git inside the request —
commit, branch, checkout, merge, log — which is cheap per call and therefore
the one worth hammering, so it gets the larger limit and the tighter watch.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="repo.run",
    label=_("Git run"),
    app_label="repo",
    unit="request",
    description=_("One init, push or pull — a worktree export plus a network git."),
    default_limit=100,
))

registry.register(Metric(
    code="repo.op",
    label=_("Git operation"),
    app_label="repo",
    unit="request",
    description=_("One git subprocess run inside the request: commit, branch, merge, log."),
    default_limit=300,
))
