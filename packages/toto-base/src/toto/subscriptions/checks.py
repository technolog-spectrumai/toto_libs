"""Build-time checks on the plan registry.

`manage.py check` runs these, which means the gate runs them and a deploy stops
on a bad ladder. Two findings, and the severity split is the point:

* **E001** — the file cannot become a registry at all (malformed, duplicate
  plan_key, unknown feature_key, no default). An Error fails `check`.
* **W001** — a feature declared paid that NO plan grants. This is the failure
  that has actually happened twice: the tile disappears for everybody,
  superusers included, and every write 402s wherever enforcement is on. Both
  times it was found by audit. A Warning rather than an Error because the
  catalogue stays open — an app may ship its own entitlements.py on a host
  whose ladder was written elsewhere.
There is deliberately NO check for "a plan grants something this host does not
mount". One was written and removed the same day: it fired seventeen times on
an ordinary lean build, because a ladder describes the PRODUCT while a build is
a subset of it — every feature a host does not switch on tripped it. A warning
that fires on a healthy system teaches people to ignore the warnings, and the
card already filters those rows out through ``registry.installed()``.
"""

from django.core.checks import Error, Warning, register


@register()
def check_subscription_plans(app_configs, **kwargs):
    from . import plans
    from .catalogue import registry

    problems = plans.validate()
    if problems:
        return [Error(f"the plan registry is unusable: {problem}",
                      id="subscriptions.E001")
                for problem in problems]

    findings = []
    granted = {key for plan in plans.all_plans() for key in plan.features}

    for entitlement in registry.all():
        if entitlement.free or entitlement.feature_key in granted:
            continue
        findings.append(Warning(
            f"{entitlement.feature_key!r} is declared paid but no plan grants "
            "it — its dashboard tile is hidden from everybody and its writes "
            "402 wherever enforcement is on",
            hint="Add it to a plan in plans.yaml, or mark it free.",
            id="subscriptions.W001"))

    return findings
