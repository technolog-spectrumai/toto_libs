"""Missions stop carrying a budget.

The reason is a durability mismatch, in the user's words: *"missions can be
deleted from db and ledger cannot."* A budget typed onto a deletable row is an
estimate wearing the clothes of a record. Company bookkeeping moves to the
assets ledger, which is append-only and hash-chained, and copying anything worth
keeping across is a human's job — there is deliberately no data migration.

Reversible, and lossy: reversing re-adds the columns (`budget_amount` is
nullable; `budget_currency` back-fills "" as an empty-string-allowed CharField)
but never the values.

Must NOT be squashed into 0006. That migration is deployed, and
portfolio/0001_initial.py names it by name — rewriting it breaks the migration
graph on every host that has ever run the Business Center.

Worth preserving from the fields it removes, because the next design will meet
the same wall: `budget_currency` was a SYMBOL and not an FK to `assets.Asset`
because that model ships in toto-economy, which only some hosts pin, while
kanban ships in toto-works — and `check_package_graph.py` forbids the edge
(toto-works depends on toto-base and nothing else).
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('kanban', '0006_mission_budget'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='mission',
            name='budget_amount',
        ),
        migrations.RemoveField(
            model_name='mission',
            name='budget_currency',
        ),
    ]
