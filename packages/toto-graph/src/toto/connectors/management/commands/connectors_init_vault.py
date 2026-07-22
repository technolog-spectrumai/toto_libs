"""Initialize the system strongbox holding API-connector credentials.

Connectors reuse the platform-wide ``sabbia-system`` strongbox (the same one
``api/admin.py`` stores API secrets into), but ``toto.sabbia`` may not be in
INSTALLED_APPS on a graph-only deployment — so its init command wouldn't be
discoverable. This thin subclass makes it available wherever connectors are.
"""

from toto.sabbia.management.commands.sabbia_init_vault import Command as SabbiaInitVaultCommand


class Command(SabbiaInitVaultCommand):
    help = (
        "Create/verify the system strongbox used for API-connector credentials "
        "(the shared sabbia-system strongbox)."
    )
