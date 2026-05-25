from django.db import migrations
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("mobilization", "0010_remove_deployment_per_diem"),
        ("response", "0001_initial"),
    ]

    operations = [
        # Re-point FKs that stay in mobilization but now point to response.Deployment
        migrations.AlterField(
            model_name="personachievement",
            name="deployment",
            field=migrations.swappable_dependency("response.Deployment") if False else
                __import__("django.db.models", fromlist=["ForeignKey"]).ForeignKey(
                    "response.Deployment",
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="achievement_awards",
                ),
        ),
        migrations.AlterField(
            model_name="emergencyequipmentaccess",
            name="deployment",
            field=__import__("django.db.models", fromlist=["ForeignKey"]).ForeignKey(
                "response.Deployment",
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="emergency_equipment",
            ),
        ),
        # Delete the 7 moved models (tables are dropped; response creates new ones)
        migrations.DeleteModel(name="DeploymentAssignment"),
        migrations.DeleteModel(name="DeploymentEquipment"),
        migrations.DeleteModel(name="DeploymentRoute"),
        migrations.DeleteModel(name="Intervention"),
        migrations.DeleteModel(name="EvacuationRoute"),
        migrations.DeleteModel(name="Deployment"),
        migrations.DeleteModel(name="InterventionType"),
    ]
