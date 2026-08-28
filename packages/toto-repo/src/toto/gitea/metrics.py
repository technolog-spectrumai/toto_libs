"""What toto.gitea meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no
models, no database, no settings.

One metric, and it is a LEVY metric: ``gitea.gb_day`` bills gigabytes of
hosted-git storage per day through the toto.tax sweep, exactly as the vault's
``storage.gb_day`` bills vault bytes. Its own code rather than a reuse,
because the levy registry refuses two providers on one metric — and because
forge storage may fairly carry a different price than vault storage.

No ``default_limit``: a windowed cap on holdings that are billed daily is
meaningless (the vault's own comment). The CAP here is the nightly
reconciler's — over the cap, the forge stops accepting new repositories.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="gitea.gb_day",
    label=_("Hosted git storage"),
    app_label="gitea",
    unit="GB-day",
    description=_("One gigabyte of repositories held on the forge for one "
                  "day. Sampled nightly; levied by the tax sweep."),
))
