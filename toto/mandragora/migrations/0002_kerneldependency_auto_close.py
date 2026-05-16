import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mandragora", "0001_initial"),
    ]

    operations = [
        # Remove the old JSON dependencies field from ComputeKernel
        migrations.RemoveField(
            model_name="computekernel",
            name="dependencies",
        ),
        # Add auto_close to ComputeKernel
        migrations.AddField(
            model_name="computekernel",
            name="auto_close",
            field=models.BooleanField(
                default=True,
                help_text="Automatically stop this kernel when the user leaves the notebook page.",
            ),
        ),
        # Create the KernelDependency model
        migrations.CreateModel(
            name="KernelDependency",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("package_name", models.CharField(max_length=255)),
                ("version_spec", models.CharField(
                    blank=True,
                    max_length=100,
                    help_text='e.g. ">=1.21", "==2.0.0", or blank for latest',
                )),
                ("install_status", models.CharField(
                    choices=[
                        ("pending", "Pending"),
                        ("installing", "Installing"),
                        ("installed", "Installed"),
                        ("failed", "Failed"),
                    ],
                    default="pending",
                    max_length=20,
                )),
                ("install_log", models.TextField(blank=True)),
                ("installed_at", models.DateTimeField(blank=True, null=True)),
                ("kernel", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="kernel_dependencies",
                    to="mandragora.computekernel",
                )),
            ],
            options={
                "verbose_name_plural": "kernel dependencies",
            },
        ),
        migrations.AlterUniqueTogether(
            name="kerneldependency",
            unique_together={("kernel", "package_name")},
        ),
    ]
