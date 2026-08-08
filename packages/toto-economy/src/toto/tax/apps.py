"""Recurring resource levies — the engine.

The tariffs pipeline is charge-before-work: a request arrives, the price is
checked, the action pays for itself. This app is the other clock: capacity ×
time. Once a day it asks every registered levy provider who holds how much of
a resource, charges the part above a staff-set free allowance through the same
``toto.quota.charge`` door as everything else, and — uniquely on this platform
— owns what happens when a charge fails with nobody watching: a calendar
warning, a week of grace, then permanent random shedding of the resource back
down to the allowance. No debt ever accrues; a day that could not be charged
is written off, which keeps the ledger strictly prepaid.

Measurement stays in the app that owns the resource (``<app>/taxes.py``,
autodiscovered below); prices stay on the tariffs rate card. This app owns
only the rules (metric ↔ allowance), the daily run, and the arrears cases.
"""

from django.apps import AppConfig


class TaxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.tax"
    label = "tax"
    verbose_name = "Tax (recurring resource levies)"

    def ready(self):
        # Mirrors QuotaConfig.ready(): every installed app may declare a levy
        # provider in <app>/taxes.py. Must not touch the database — provider
        # modules register pure objects and defer model imports to call time.
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("taxes")
