"""What vault meters.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="storage.request",
    label=_("File upload"),
    app_label="vault",
    unit="request",
    description=_("One file accepted through a gateway or the file API."),
    default_limit=500,
))

registry.register(Metric(
    code="storage.transfer_mb",
    label=_("Bytes transferred"),
    app_label="vault",
    unit="mb",
    description=_("Megabytes moved by an upload. Charged by size, not per call."),
    default_limit=2000,
))

# Levied by the clock, not the click: toto.tax samples each owner's stored
# bytes once a day and charges every byte held. No
# default_limit — a cap on holdings already billed by the day is meaningless,
# and its absence keeps ingress_quota from seeding a policy row.
registry.register(Metric(
    code="storage.gb_day",
    label=_("Storage held"),
    app_label="vault",
    unit="gb_day",
    description=_("Gigabytes stored, sampled nightly and billed from the first byte. "
                  "The rule that arms it lives in toto.tax; the price on the rate card."),
))

# Bytes OUT, the mirror of storage.transfer_mb — and deliberately weaker.
# Measured on every Django-served download (the public download door, the
# peer bytes door, the encrypted-download door) and cappable by a staff
# policy row; NOT charged: pricing egress means deciding who pays when the
# downloader is anonymous, and until that is decided out loud a Tariff row
# on this code prices nothing. No default_limit — uncapped by default, so
# ingress_quota seeds no policy and nothing is refused until a host chooses
# a number. What nginx serves straight from disk (/media/) never reaches
# Django and is not counted — the same honesty note as gitea pushes.
registry.register(Metric(
    code="storage.egress_mb",
    label=_("Bytes served"),
    app_label="vault",
    unit="mb",
    description=_("Megabytes served by a download, billed to the file's owner. "
                  "Measured and cappable; deliberately not charged — a price "
                  "on this metric prices nothing today."),
))
