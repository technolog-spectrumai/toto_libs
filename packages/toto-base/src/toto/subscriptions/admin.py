from django import forms
from django.contrib import admin

from .models import (
    CommunityDiscount,
    CommunityPlanOffer,
    Subscription,
    SubscriptionCharge,
    SubscriptionQuotaPolicy,
)


def _plan_key_choices():
    """Registry keys, for every admin field that names a plan.

    The eligibility administration is populated FROM the registry — no plan
    definition is copied into the database to render it, which is the whole
    point of the file being the only authority.
    """
    from . import plans

    return [(plan.key, f"{plan.name} ({plan.key})") for plan in plans.all_plans()]


# NO SubscriptionPlanAdmin. Plans stopped being rows on 2026-09-02 — they are
# read-only objects from plans.yaml, so there is nothing here to add, change
# or delete. The admin was a live second authoring path beside the seeder,
# with list_editable on units and a prepopulated code field, and two editors
# for one ladder is exactly what the file replaced. The plans page IS the
# read-only view, and staff see every plan on it.


class _SuperusersOnly:
    """Plans and who may hold them are the admin's (2026-09-26): staff see
    none of these rows. `has_module_permission` hides the section too."""

    def has_module_permission(self, request):
        return bool(request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_add_permission(self, request):
        return bool(request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_superuser)


class SubscriptionAdminForm(forms.ModelForm):
    """`plan_key` is a choice from the ladder, and the same two refusals
    `services.subscribe` makes: an admin-only plan needs a superuser, a paid
    plan needs an offer from a Community of the person's (staff and
    superusers excepted, as `is_eligible` says)."""

    class Meta:
        model = Subscription
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "plan_key" in self.fields:
            self.fields["plan_key"] = forms.ChoiceField(choices=_plan_key_choices())

    def clean(self):
        cleaned = super().clean()
        user, key = cleaned.get("user"), cleaned.get("plan_key")
        if user is None or not key:
            return cleaned
        from . import plans, services

        plan = plans.get(key)
        if plan is None:
            raise forms.ValidationError({"plan_key": "No such plan in plans.yaml."})
        if plan.admin_only and not user.is_superuser:
            raise forms.ValidationError({"plan_key": f"{plan.name} is for superusers only."})
        if not plan.is_default and not services.is_eligible(user, key):
            raise forms.ValidationError({"plan_key": (
                f"No Community {user} belongs to offers {plan.name}. Offer it on the "
                "Communities tab first.")})
        return cleaned


@admin.register(CommunityDiscount)
class CommunityDiscountAdmin(_SuperusersOnly, admin.ModelAdmin):
    # One number per community, every plan. The Discounts tab is the everyday
    # door; this is the escape hatch.
    list_display = ("community", "percent")
    list_editable = ("percent",)
    autocomplete_fields = ("community",)
    search_fields = ("community__name",)


@admin.register(Subscription)
class SubscriptionAdmin(_SuperusersOnly, admin.ModelAdmin):
    form = SubscriptionAdminForm
    list_display = ("user", "plan_key", "state", "anchor_date", "expires_at", "lapse_reason")
    list_filter = ("state", "plan_key")
    search_fields = ("user__username", "user__email", "plan_key")
    autocomplete_fields = ("user",)
    # Frozen after creation: the anchor defines every period label, which is
    # half the idempotency key of every charge this subscription will ever have.
    readonly_fields = ("anchor_date", "started_at", "changed_at", "lapse_reason", "forced")


@admin.register(SubscriptionCharge)
class SubscriptionChargeAdmin(admin.ModelAdmin):
    list_display = ("subscription", "period_label", "plan_key", "units",
                    "discount_percent", "status", "settled_at")
    list_filter = ("status",)
    search_fields = ("subscription__user__username", "period_label")
    readonly_fields = ("subscription", "period_label", "plan_key", "plan_name", "units",
                       "discount_percent", "discount_source", "settled_at",
                       "created_at")

    def has_add_permission(self, request):
        # Charges are materialized from a subscription, never hand-made: a row
        # with a period label nothing produced would break the idempotency the
        # unique constraint exists to give.
        return False


@admin.register(SubscriptionQuotaPolicy)
class SubscriptionQuotaPolicyAdmin(admin.ModelAdmin):
    list_display = ("metric_code", "limit", "period", "mode", "active")


@admin.register(CommunityPlanOffer)
class CommunityPlanOfferAdmin(_SuperusersOnly, admin.ModelAdmin):
    # NO ROWS FOR A PLAN MEANS IT IS OFFERED TO NOBODY. That inverts the rule
    # the PlanAudience table had, so deleting the last row here does NOT make
    # a plan public — it takes it off the market. The Communities tab is the
    # everyday door and shows the state plainly; this is the escape hatch.
    list_display = ("community", "plan_key", "created_at")
    list_filter = ("plan_key",)
    autocomplete_fields = ("community",)
    search_fields = ("community__name", "plan_key")

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "plan_key":
            kwargs["widget"] = admin.widgets.AdminRadioSelect()
            kwargs["choices"] = _plan_key_choices()
            return db_field.formfield(**kwargs)
        return super().formfield_for_dbfield(db_field, request, **kwargs)
