"""Re-export. The policy lives in ``toto.vault.access``.

It was written here, in toto-media-ops — a wheel most hosts do not pin — which
meant the vault could not consult its own read rule and grew a weaker one
instead. It moved to the app that owns the files; this alias stays so manta and
anything else that imported it keeps working.
"""

from toto.vault.access import may_read as user_can_access_vault_file  # noqa: F401
