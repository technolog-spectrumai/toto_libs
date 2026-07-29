from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(key="wallet", title="Wallet", order=20)
class WalletProfilePlugin(ProfilePlugin):
    template_name = "assets/profile_plugins/wallet.html"
    section_icon = "fa-solid fa-wallet"
    show_for_owner_only = True

    def is_visible_for_profile(self, **kwargs) -> bool:
        profile = kwargs.get("profile")
        if not profile:
            return False
        return bool(getattr(profile, "user", None))

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        profile = kwargs.get("profile")
        user = getattr(profile, "user", None)
        if user:
            from toto.assets.models import LedgerAccount
            accounts = list(
                LedgerAccount.objects.filter(user=user, active=True)
                .order_by("-user_priority", "code")
                .prefetch_related("holdings__asset")
            )
            top_account = accounts[0] if accounts else None
            nonzero_holdings = [
                h
                for acc in accounts
                for h in acc.holdings.all()
                if h.balance_base_units > 0
            ]
            context.update({
                "wallet_accounts": accounts,
                "wallet_top_account": top_account,
                "wallet_holdings": nonzero_holdings[:6],
                "wallet_account_count": len(accounts),
            })
        return context
