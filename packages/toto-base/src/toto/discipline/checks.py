"""Checked when the platform starts (``manage.py check``).

* **E001** the Discipline file cannot be read or holds something it may not:
  an unknown key, a negative number, a price for a metric no installed app
  meters, a community given two auras.
* **E002** ``toto.socialhub`` is not installed: an aura is a community's.
* **W001** the ledger's rate card and Discipline are both installed and the
  host does not say which charges (``settings.QUOTA_CHARGER``): the rate card
  does, and Discipline's prices are read by nothing.
"""

from django.core.checks import Error, Warning, register


@register()
def check_discipline(app_configs, **kwargs):
    from django.apps import apps
    from django.conf import settings

    from . import config

    findings = []
    if not apps.is_installed("toto.socialhub"):
        findings.append(Error("toto.discipline needs toto.socialhub: an aura is a community's",
                              id="discipline.E002"))
    findings.extend(Error(f"the Discipline file is unusable: {problem}", id="discipline.E001")
                    for problem in config.validate())
    if apps.is_installed("toto.tariffs") and not getattr(settings, "QUOTA_CHARGER", ""):
        findings.append(Warning(
            "toto.tariffs and toto.discipline are both installed and QUOTA_CHARGER is not "
            "set: the rate card charges, and Discipline's prices are read by nothing",
            hint='Set QUOTA_CHARGER = "discipline" or "tariffs".', id="discipline.W001"))
    return findings
