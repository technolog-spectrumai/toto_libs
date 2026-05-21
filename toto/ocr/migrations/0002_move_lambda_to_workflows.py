import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ocr", "0001_initial"),
        ("workflows", "0002_lambdafunction_move"),
    ]

    operations = [
        migrations.AlterField(
            model_name="imagetransform",
            name="lambda_function",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="image_transforms",
                to="workflows.lambdafunction",
            ),
        ),
    ]
