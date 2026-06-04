from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="obligations",
    title="Obligations",
    order=20,
)
class ObligationsProfilePlugin(ProfilePlugin):
    template_name = "claims/profile_plugins/obligations.html"
    section_icon = "fa-solid fa-handshake-angle"
    show_for_owner_only = True

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        profile = kwargs["profile"]
        user = profile.user

        from toto.assets.models import LedgerAccount, Obligation, ObligationStatus

        user_accounts = LedgerAccount.objects.filter(user=user, active=True)
        outstanding = list(
            Obligation.objects.filter(
                debtor_account__in=user_accounts,
                status=ObligationStatus.PENDING,
            )
            .select_related("asset", "debtor_account")
            .order_by("due_at")[:10]
        )
        total_count = Obligation.objects.filter(
            debtor_account__in=user_accounts,
            status=ObligationStatus.PENDING,
        ).count()

        context["outstanding_obligations"] = outstanding
        context["outstanding_obligation_count"] = total_count
        return context
