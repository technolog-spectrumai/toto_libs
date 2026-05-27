from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("instruments", "0001_initial"),
        ("assets", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="SubscriptionPayment",
        ),
        migrations.DeleteModel(
            name="SubscriptionContract",
        ),
    ]
