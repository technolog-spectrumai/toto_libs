"""What the forum meters (2026-09-25). Pure data; imported by QuotaConfig.

* `forum.message` — one ordinary message stored. Security mana: talking in
  the clear is what the security pool prices (economy.md, mana colours).
* `forum.encrypt` — one message sealed under a room key. Compute mana.
* `forum.room_key` — one encrypted room's key made and wrapped. Compute mana.

Edits, deletes, reads and typing are free.
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

for code, label, unit, limit, description in (
    ("forum.message", _("Forum message"), "message", 2000,
     "One message stored in an ordinary (unencrypted) room."),
    ("forum.encrypt", _("Encrypted forum message"), "message", 2000,
     "One message sealed under an encrypted room's key."),
    ("forum.room_key", _("Encrypted room key"), "key", 20,
     "One encrypted room's key made and wrapped."),
):
    registry.register(Metric(code=code, label=label, app_label="forum", unit=unit,
                             description=description, default_limit=limit))
