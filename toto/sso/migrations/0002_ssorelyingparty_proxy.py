from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("sso", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SSORelyingParty",
            fields=[],
            options={
                "ordering": ["name"],
                "verbose_name": "SSO relying party",
                "verbose_name_plural": "SSO relying parties",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("sso.ssoclient",),
        ),
    ]
