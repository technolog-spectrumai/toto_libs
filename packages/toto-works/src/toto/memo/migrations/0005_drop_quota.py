"""Drop the quota pair.

Memo's PDF export moved to the zinnia desktop app, taking the only metric that
ever wrote these tables (`memo.pdf`). No price was ever seeded for it
(TARIFF_SEED_PRICES is off on every host), so the rows are metering history and
nothing reads them.

Irreversible by intent: re-adding empty tables would be a lie about what the
app does.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("memo", "0004_remove_memoquotapolicy_memo_memoquotapolicy_one_default_and_more")]

    operations = [
        migrations.DeleteModel(name="MemoQuotaPolicy"),
        migrations.DeleteModel(name="MemoUsageEvent"),
    ]
