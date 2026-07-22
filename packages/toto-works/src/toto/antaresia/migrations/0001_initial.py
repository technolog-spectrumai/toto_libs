from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('vault', '0001_initial'),
        ('workflows', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PythonRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('task_id', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('running', 'Running'),
                        ('success', 'Success'),
                        ('failed', 'Failed'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('stdout', models.TextField(blank=True)),
                ('stderr', models.TextField(blank=True)),
                ('exit_code', models.IntegerField(blank=True, null=True)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('vault_file', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='python_runs',
                    to='vault.vaultfile',
                )),
                ('workflow_run', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='python_runs',
                    to='workflows.workflowrun',
                )),
            ],
            options={
                'ordering': ['-started_at'],
            },
        ),
    ]
