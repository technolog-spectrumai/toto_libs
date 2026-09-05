"""Drop the usage-event and quota-policy tables: the forge is billed per seat.

`gitea.gb_day` billed gigabytes of hosted-git storage per day, the same shape
as the vault's levy. It was removed on 2026-09-05 because access to the forge
is already sold per SEAT — `gitea` and `repo` are entitlements on the
Professional plan — so charging again by the gigabyte was an overlapping fee
on one feature.

**Nothing is lost that anybody paid.** The rule was seeded `active=False` and
no seeder ever gave the metric a price, so the levy never charged a single
account. These two tables are empty on every host that has them.

WHAT THIS MIGRATION DOES NOT TOUCH, and the distinction is the point: the
nightly task in `tasks.py` samples forge storage AND reconciles a cap. The cap
stops the forge accepting new repositories when an account is over its limit,
which is disk safety rather than billing and still earns its place on a 75 GB
box. `GiteaAccount.storage_bytes`, `storage_cap_gb`, `repo_creation_blocked`
and the whole `GiteaForgeSample` table stay exactly as they were.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("gitea", "0002_giteaforgesample_giteaquotapolicy_and_more"),
    ]

    operations = [
        migrations.DeleteModel(name="GiteaUsageEvent"),
        migrations.DeleteModel(name="GiteaQuotaPolicy"),
    ]
