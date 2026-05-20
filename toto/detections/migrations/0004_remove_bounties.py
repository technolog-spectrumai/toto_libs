from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("detections", "0003_detection_help_links"),
    ]

    operations = [
        migrations.DeleteModel(name="BountyPayment"),
        migrations.DeleteModel(name="BountyReview"),
        migrations.DeleteModel(name="BountySubmission"),
        migrations.DeleteModel(name="BountyClaim"),
        migrations.DeleteModel(name="Bounty"),
        migrations.DeleteModel(name="BountyBoard"),
    ]
