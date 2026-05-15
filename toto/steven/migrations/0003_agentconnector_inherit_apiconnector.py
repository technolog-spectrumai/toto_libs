import django.db.models.deletion
import django_jsonform.models.fields
import toto.core.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gervazy", "0002_add_new_models"),
        ("steven", "0002_update_agentconnector"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Rename related_name on api_secret to match the %(class)s pattern in the abstract base.
        # No DB change — purely updating Django's migration state.
        migrations.AlterField(
            model_name="agentconnector",
            name="api_secret",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="agentconnector_api_connectors",
                to="gervazy.encryptedsecret",
                help_text="Gervazy EncryptedSecret holding the OpenAI API key.",
            ),
        ),
        # Add fields from ApiConnector abstract base not previously on AgentConnector.
        migrations.AddField(
            model_name="agentconnector",
            name="base_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Base API URL, e.g. https://api.openai.com/v1/",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="agentconnector",
            name="auth_type",
            field=models.CharField(
                choices=[
                    ("none", "No authentication"),
                    ("api_key_header", "API key header"),
                    ("bearer_token", "Bearer token"),
                    ("query_param", "Query parameter"),
                    ("hmac", "HMAC signature"),
                    ("signature", "Asymmetric request signature"),
                    ("custom", "Custom"),
                ],
                default="bearer_token",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="agentconnector",
            name="signing_key",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="agentconnector_signing_keys",
                to="gervazy.encryptedprivatekey",
                help_text="Encrypted private key for signed API requests.",
            ),
        ),
        migrations.AddField(
            model_name="agentconnector",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agentconnector_owned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="agentconnector",
            name="auth_config",
            field=django_jsonform.models.fields.JSONField(
                blank=True,
                default=dict,
                help_text="Non-secret auth config such as header name or timeout.",
            ),
        ),
        migrations.AddField(
            model_name="agentconnector",
            name="extra",
            field=django_jsonform.models.fields.JSONField(
                blank=True,
                default=dict,
                validators=[toto.core.models._reject_secret_like_json],
                help_text="Non-secret provider-specific configuration.",
            ),
        ),
    ]
