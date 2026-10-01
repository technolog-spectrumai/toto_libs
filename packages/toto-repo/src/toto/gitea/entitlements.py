"""The forge's own entry in the plan catalogue.

Declared by the app, not in `toto.subscriptions.catalogue`'s defaults, since
2026-10-01: a host that installs toto.gitea sells "Git hosting", and a host
that does not — zenobia, whose Gitea moved to a machine of its own — has no
such feature in its catalogue for a plan to name. `SubscriptionsConfig.ready`
autodiscovers this module before it validates the ladder.
"""

from toto.subscriptions.catalogue import Entitlement, registry

registry.register(Entitlement("gitea", "Git hosting", order=45,
                              icon="fa-brands fa-git-alt",
                              description="Repositories hosted on this platform."))
