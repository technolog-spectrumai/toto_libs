import django.core.validators
import django.db.models.deletion
import django_jsonform.models.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("gervazy", "0002_add_new_models"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Add ApiConnector model
        migrations.CreateModel(
            name="ApiConnector",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(blank=True, max_length=140, unique=True)),
                ("provider", models.CharField(
                    choices=[("generic","Generic"),("openai","OpenAI"),("anthropic","Anthropic"),("github","GitHub"),("custom","Custom")],
                    default="generic", max_length=40,
                )),
                ("base_url", models.URLField(blank=True, help_text="Base API URL, e.g. https://api.openai.com/v1/")),
                ("auth_type", models.CharField(
                    choices=[("none","No authentication"),("api_key_header","API key header"),("bearer_token","Bearer token"),("query_param","Query parameter"),("hmac","HMAC signature"),("signature","Asymmetric request signature"),("custom","Custom")],
                    default="bearer_token", max_length=40,
                )),
                ("auth_config", django_jsonform.models.fields.JSONField(
                    blank=True,
                    default=dict,
                    help_text="Non-secret auth config such as header name or timeout.",
                )),
                ("extra", django_jsonform.models.fields.JSONField(
                    blank=True,
                    default=dict,
                    help_text="Non-secret provider-specific configuration.",
                )),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("api_secret", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="api_connectors",
                    to="gervazy.encryptedsecret",
                )),
                ("signing_key", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="api_connectors",
                    to="gervazy.encryptedprivatekey",
                )),
                ("owner", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="api_connectors",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["name"]},
        ),

        # Remove old Platform FK fields (SecretKey, RSAKeyPair)
        migrations.RemoveField(model_name="platform", name="secret"),
        migrations.RemoveField(model_name="platform", name="api_keypair_out"),
        migrations.RemoveField(model_name="platform", name="api_keypair_in"),

        # Add new Platform fields
        migrations.AddField(
            model_name="platform",
            name="signing_secret",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="platform_signing_secrets",
                to="gervazy.encryptedsecret",
                help_text="Encrypted secret used for platform token signing.",
            ),
        ),
        migrations.AddField(
            model_name="platform",
            name="api_signing_key_out",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="platform_outbound_signing",
                to="gervazy.encryptedprivatekey",
                help_text="Encrypted private key for outbound signed sync requests.",
            ),
        ),
        migrations.AddField(
            model_name="platform",
            name="api_verify_key_in",
            field=models.TextField(
                blank=True, null=True,
                help_text="Public key PEM used to verify incoming sync requests.",
            ),
        ),
        migrations.AddField(
            model_name="platform",
            name="api_connector",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="platforms",
                to="core.apiconnector",
                help_text="Generic API connector for outbound platform calls.",
            ),
        ),
    ]
