"""What locations meters: server-side geocoding, one lookup at a time.

Imported from QuotaConfig.ready(), so this module stays pure data — no models,
no database, no settings.

A lookup is a place name or a map point resolved by the host's geocoding
provider (2026-09-28). Clicking the map, dropping a pin and reading the map are
free; only a question the server has to put to the provider is metered, and
only when it answers (``toto/locations/geocoding.py`` says what is and is not
charged). Compute mana.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

registry.register(Metric(
    code="locations.geocode",
    label=_("Place lookup"),
    app_label="locations",
    unit="lookup",
    description=_("One place name or map point resolved on the server."),
    default_limit=200,
))
