from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("mandragora", "0002_kerneldependency_auto_close"),
        ("workflows", "0002_lambdafunction_move"),
        ("ocr", "0002_move_lambda_to_workflows"),
    ]

    operations = [
        migrations.DeleteModel(
            name="LambdaFunction",
        ),
    ]
