"""Every asset gets a short name (2026-09-30): four capital letters, unique,
the key of its page (/assets/assets/FLOR/).

Existing rows keep a code that is already four free capital letters; the
seeded tickers get the codes the owner chose; anything else gets the ticker's
letters, padded with X, varied at the tail until free — the same rule as
``models.derive_asset_code``, copied here because a migration must not import
the model module it migrates.
"""

import itertools
import re
import string

import django.core.validators
from django.db import migrations, models

CHOSEN = {"ASR": "ASAR", "TPLN": "FLOR", "FLOR": "FLOR", "BLUE": "SECU", "RED": "COMP",
          "GREEN": "STOR", "MANA": "MANA", "BANANA": "BNNA", "MAKARONI": "MACA"}


def _derive(unit_name, taken):
    letters = "".join(ch for ch in (unit_name or "").upper() if "A" <= ch <= "Z")
    base = (letters + "XXXX")[:4]
    if base not in taken:
        return base
    for width in range(1, 5):
        for tail in itertools.product(string.ascii_uppercase, repeat=width):
            candidate = base[:4 - width] + "".join(tail)
            if candidate not in taken:
                return candidate
    raise RuntimeError("every four-letter asset code is taken")


def fill_codes(apps, schema_editor):
    Asset = apps.get_model("assets", "Asset")
    taken = set()
    rows = list(Asset.objects.order_by("pk"))
    # Pass 1: keep what is already a valid, unclaimed code.
    keep = {}
    for asset in rows:
        if re.fullmatch(r"[A-Z]{4}", asset.code or "") and asset.code not in taken:
            keep[asset.pk] = asset.code
            taken.add(asset.code)
    # Pass 2: the chosen codes, then derived ones.
    for asset in rows:
        if asset.pk in keep:
            continue
        chosen = CHOSEN.get(asset.unit_name)
        code = chosen if chosen and chosen not in taken else _derive(asset.unit_name, taken)
        taken.add(code)
        Asset.objects.filter(pk=asset.pk).update(code=code)


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0009_faucet_sources"),
    ]

    operations = [
        migrations.RunPython(fill_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="asset",
            name="code",
            field=models.CharField(
                help_text="Four capital letters, e.g. FLOR — the key of the asset's page.",
                max_length=4, unique=True,
                validators=[django.core.validators.RegexValidator(
                    "^[A-Z]{4}$", "Four capital letters, A–Z.")],
                verbose_name="short name"),
        ),
    ]
