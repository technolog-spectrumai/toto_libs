"""What Anastasia meters: one execution.

Deliberately ONE metric, and deliberately not four.

The obvious design is to meter the resources — CPU-seconds, RAM-hours — because
that is what a compute service sells. It is the wrong thing to meter *here*.
Reserved capacity is what costs this deployment something, and reserved
capacity is held by a lease over TIME, which is a levy, not a per-request
count: the `toto.tax` engine already exists for exactly that shape and bills
`storage.gb_day` the same way. Metering resources per request as well would
charge twice for one thing.

So this metric is a **rate limit on submissions**, not a price on compute: it
stops one person queueing a thousand jobs into a Capsule and walking away, which
is `aralia.render`'s reasoning (async work is not self-limiting because nobody
waits for it). 200 rather than aralia's 20 because an execution is bounded by a
Capsule the user already paid attention to reserve — the Capsule is the real cap, and
this is the backstop behind it.

TODO (portal/anastasia.md): the reservation levy — `anastasia.capsule_hour` over
the four reserved dimensions, including warm runners, as a LevyProvider in
`taxes.py`. Not in this pass; the ledger work is separate from the compute work.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="anastasia.execution",
    label=_("Compute execution"),
    app_label="anastasia",
    unit="request",
    description=_(
        "One heavy job — a PDF render, a LaTeX compile, a media conversion, an "
        "OCR scan — run in a disposable runner inside one of your Compute Capsules."
    ),
    default_limit=200,
))
