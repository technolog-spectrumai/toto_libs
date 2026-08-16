"""Drop the quota pair.

`cyprian.pdf` was the only metric that ever wrote these tables, and it left with
the PDF export. No price was ever seeded for it, so the rows are metering
history that nothing reads.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("cyprian", "0002_remove_cyprianquotapolicy_cyprian_cyprianquotapolicy_one_default_and_more")]

    operations = [
        migrations.DeleteModel(name="CyprianQuotaPolicy"),
        migrations.DeleteModel(name="CyprianUsageEvent"),
    ]
