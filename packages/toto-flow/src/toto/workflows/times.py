"""Workflows' one time dial: how long each async lambda step may run.

Imported from QuotaConfig.ready(), so this module stays pure data.

Naming note: ``scope="workspace"`` mechanically means "object-scoped"
throughout toto.quota.times and toto.tax.timegrants — the scoped object here
is the Workflow row itself. Only its owner may set the dial and the owner
pays the demurrage; a system workflow (owner NULL) can never be dialed
(timegrants' ownership check refuses every actor).

The free default mirrors WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS' fallback in
services/executor.py — keep the two in step. The ceiling is infrastructure
arithmetic: a lambda step's celery hard limit is timeout + 5, so 300 → 305 s,
far under the global 1500 s soft limit, the stuck-run sweep floors (node
10800 s, run 21600 s) and the Redis visibility timeout (14700 s).
"""

from toto.quota.times import TimeLimit, registry

registry.register(TimeLimit(
    key="workflows.lambda_timeout",
    label="Lambda step timeout",
    app_label="workflows",
    scope="workspace",              # = object-scoped; see module docstring
    scope_model="workflows.Workflow",
    scope_owner_attr="owner_id",
    free_seconds=30,
    ceiling_seconds=300,
    display_unit="minutes",
    description="How long each asynchronous lambda step of this workflow may "
                "run before it is killed. Applies to runs started after "
                "changing it; a longer kernel-configured timeout still wins.",
))
