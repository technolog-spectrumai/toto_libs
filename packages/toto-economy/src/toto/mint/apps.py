from django.apps import AppConfig


class MintConfig(AppConfig):
    """The issuance desk — where new assets come from.

    Installed on the MASTER only. A branch cannot run code it does not have,
    which is why this is a separate app rather than a page inside toto.assets:
    the ledger ships everywhere, the mint does not.

    It creates new fixed-supply assets. It can never increase the supply of an
    existing one — there is no mint verb in this platform's ledger and never
    will be. More of something in circulation means releasing from its
    reserve; something genuinely new means a new asset with a new identity.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.mint"
    label = "mint"
    verbose_name = "Mint (asset issuance)"
