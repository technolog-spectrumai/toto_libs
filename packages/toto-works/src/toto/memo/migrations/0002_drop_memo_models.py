from django.db import migrations


class Migration(migrations.Migration):
    """Drop all memo DB models; memo is now a file-based presentation viewer/editor."""

    dependencies = [
        ("memo", "0001_initial"),
    ]

    operations = [
        # MemoCard references MemoDeck + MemoDiagram — delete it first.
        migrations.DeleteModel("MemoCard"),
        # MemoDeck owns the M2M to Tag — drop it before Tag.
        migrations.DeleteModel("MemoDeck"),
        migrations.DeleteModel("MemoDiagram"),
        migrations.DeleteModel("Tag"),
    ]
