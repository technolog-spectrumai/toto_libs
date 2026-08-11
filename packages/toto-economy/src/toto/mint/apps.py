from django.apps import AppConfig


class MintConfig(AppConfig):
    """The issuance desk — where new assets come from.

    Installed on the MASTER only. A branch cannot run code it does not have,
    which is why this is a separate app rather than a page inside toto.assets:
    the ledger ships everywhere, the mint does not.

    Three of the four monetary verbs live here: ENGRAVE creates a currency's
    identity and its permanent maximum, MINT brings units into existence up to
    that maximum, and BURN destroys units held in the reserve. The fourth,
    DISTRIBUTE, is an ordinary transfer and belongs to the ledger.

    Every mint and every burn is an immutable signed event on one append-only
    chain, and supply is the sum over it. There is no supply column anyone can
    edit, and a maximum can never be raised — needing more than the ceiling
    means engraving a new currency, because a promise you can raise is not one.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.mint"
    label = "mint"
    verbose_name = "Mint (asset issuance)"
