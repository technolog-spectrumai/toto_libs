from decimal import Decimal

from django.db import migrations


def seed_fixed_exchange_rates(apps, schema_editor):
    Asset = apps.get_model("assets", "Asset")
    AssetExchangeRate = apps.get_model("assets", "AssetExchangeRate")
    AssetExchangeRateHistory = apps.get_model("assets", "AssetExchangeRateHistory")

    specs = [
        ("TUSD", "TPLN", Decimal("4.000000000000")),
        ("TEUR", "TPLN", Decimal("4.300000000000")),
        ("TEUR", "TUSD", Decimal("1.080000000000")),
    ]

    for from_unit, to_unit, rate in specs:
        from_asset = Asset.objects.filter(unit_name=from_unit).first()
        to_asset = Asset.objects.filter(unit_name=to_unit).first()
        if not from_asset or not to_asset:
            continue

        exchange_rate, _ = AssetExchangeRate.objects.update_or_create(
            from_asset=from_asset,
            to_asset=to_asset,
            defaults={
                "rate": rate,
                "commission_percent": Decimal("1.5000"),
                "active": True,
                "metadata": {"source": "fixed_seed_migration"},
            },
        )
        AssetExchangeRateHistory.objects.create(
            exchange_rate=exchange_rate,
            from_asset=from_asset,
            to_asset=to_asset,
            rate=rate,
            commission_percent=Decimal("1.5000"),
            metadata={"source": "fixed_seed_migration", "active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0006_assetexchangerate_assetexchangerequest_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_fixed_exchange_rates, migrations.RunPython.noop),
    ]
