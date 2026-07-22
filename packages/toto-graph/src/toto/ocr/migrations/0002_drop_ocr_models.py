"""Drop all OCR models.

The OCR app was repurposed into a stateless Knowledge-Graph sub-tab
(upload screenshot → Tesseract → text → ingestor). None of the old
workspace/image/transform models are used any more.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ocr", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="OcrLine"),
        migrations.DeleteModel(name="ImageTransformParam"),
        migrations.DeleteModel(name="OcrImage"),
        migrations.DeleteModel(name="ImageTransform"),
        migrations.DeleteModel(name="OcrProject"),
    ]
