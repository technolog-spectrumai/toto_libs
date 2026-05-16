# State-only migration: EmailService physical table stays as socialhub_emailservice,
# owned by socialhub.0001_initial DDL. We only update the ORM state to point FK
# at api.EmailService and remove the socialhub.EmailService model from the state.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
        ('socialhub', '0002_alter_community_head_alter_referencerequest_referrer_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='community',
                    name='email_service',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='community_emails', to='api.emailservice'),
                ),
                migrations.DeleteModel(
                    name='EmailService',
                ),
            ],
            database_operations=[],
        ),
    ]
