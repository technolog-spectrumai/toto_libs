from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from toto.assets.models import Asset, AssetExchangeRequest, LedgerAccount, Tokenization, to_base_units


class TokenizationCreateForm(forms.Form):
    asset_name = forms.CharField(max_length=255, label="Asset name")
    unit_name = forms.CharField(max_length=20, label="Unit name")
    decimals = forms.IntegerField(min_value=0, max_value=19, initial=0)
    total_supply = forms.DecimalField(max_digits=24, decimal_places=8, min_value=Decimal("0.00000001"))
    reserve_account = forms.ModelChoiceField(
        queryset=LedgerAccount.objects.filter(active=True).order_by("code"),
        help_text="Initial supply is issued to this reserve account.",
    )
    supervisor = forms.ModelChoiceField(
        queryset=None,
        required=False,
        help_text="Person responsible for supervising this tokenization.",
    )
    is_currency = forms.BooleanField(required=False, label="Accepted as payment currency")
    backing_document = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Describe the physical, legal, or custodial backing for this token.",
    )
    minting_authority = forms.CharField(max_length=255, required=False)
    metadata = forms.JSONField(required=False, initial=dict, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, real_world_object=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.real_world_object = real_world_object

        from toto.people.models import Person

        self.fields["supervisor"].queryset = Person.objects.order_by("display_name")
        for field in self.fields.values():
            css_class = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css_class}".strip()

    def clean_unit_name(self):
        unit_name = self.cleaned_data["unit_name"].strip().upper()
        if Asset.objects.filter(unit_name__iexact=unit_name).exists():
            raise forms.ValidationError("An asset with this unit name already exists.")
        return unit_name

    def clean(self):
        cleaned = super().clean()
        if self.real_world_object and Tokenization.objects.filter(real_world_object=self.real_world_object).exists():
            raise forms.ValidationError("This object is already tokenized and cannot be tokenized again.")
        return cleaned


class ExchangeRequestCreateForm(forms.Form):
    requester_account = forms.ModelChoiceField(
        queryset=LedgerAccount.objects.none(),
        widget=forms.RadioSelect,
        label="Pay from",
    )
    counterparty = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Send to",
        help_text="Leave blank to publish this as a public ask.",
    )
    offer_asset = forms.ModelChoiceField(queryset=Asset.objects.filter(active=True, is_currency=True).order_by("unit_name"))
    offer_amount = forms.DecimalField(max_digits=24, decimal_places=8, min_value=Decimal("0.00000001"))
    request_asset = forms.ModelChoiceField(queryset=Asset.objects.filter(active=True, is_currency=True).order_by("unit_name"))
    expected_amount = forms.DecimalField(
        max_digits=24,
        decimal_places=8,
        min_value=Decimal("0.00000001"),
        label="Expected amount",
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        user_model = get_user_model()
        self.fields["counterparty"].queryset = user_model.objects.exclude(pk=getattr(user, "pk", None)).order_by("username")
        if user and user.is_authenticated:
            accounts = LedgerAccount.objects.filter(user=user, active=True).order_by("code")
            self.fields["requester_account"].queryset = accounts
            if not self.is_bound and accounts.count() == 1:
                self.initial["requester_account"] = accounts.first().pk
        self._style_fields()

    def _style_fields(self):
        css_class = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
        for name, field in self.fields.items():
            existing = field.widget.attrs.get("class", "")
            if name == "requester_account":
                field.widget.attrs["class"] = f"{existing} h-4 w-4 accent-current".strip()
                continue
            field.widget.attrs["class"] = f"{existing} {css_class}".strip()

    @property
    def requester_account_options(self):
        from toto.assets.models import AssetHolding

        selected = self["requester_account"].value()
        options = []
        for account in self.fields["requester_account"].queryset:
            holdings = (
                AssetHolding.objects
                .filter(account=account, balance_base_units__gt=0, asset__active=True, asset__is_currency=True)
                .select_related("asset")
                .order_by("asset__unit_name")[:4]
            )
            options.append({
                "account": account,
                "holdings": holdings,
                "selected": str(selected or "") == str(account.pk),
            })
        return options

    def clean(self):
        cleaned = super().clean()
        offer_asset = cleaned.get("offer_asset")
        request_asset = cleaned.get("request_asset")
        offer_amount = cleaned.get("offer_amount")
        expected_amount = cleaned.get("expected_amount")
        account = cleaned.get("requester_account")
        if account and self.user and account.user_id != self.user.id:
            self.add_error("requester_account", "Choose one of your own active accounts.")
        if offer_asset and request_asset and offer_asset.pk == request_asset.pk:
            self.add_error("request_asset", "Choose a different requested asset.")
        if offer_amount and expected_amount:
            cleaned["implied_rate"] = expected_amount / offer_amount
        return cleaned

    def save(self):
        data = self.cleaned_data
        return AssetExchangeRequest.objects.create(
            requester=self.user,
            counterparty=data.get("counterparty"),
            requester_account=data["requester_account"],
            offer_asset=data["offer_asset"],
            request_asset=data["request_asset"],
            offer_amount_base_units=to_base_units(data["offer_amount"], data["offer_asset"].decimals),
            request_amount_base_units=to_base_units(data["expected_amount"], data["request_asset"].decimals),
            commission_amount_base_units=0,
            exchange_rate=data["implied_rate"],
            commission_percent=Decimal("0"),
            note=data.get("note", ""),
            metadata={"rate_source": "proposal"},
        )


class ExchangeRequestResponseForm(forms.Form):
    counterparty_account = forms.ModelChoiceField(queryset=LedgerAccount.objects.none())
    response_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, user=None, exchange_request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.exchange_request = exchange_request
        if user and user.is_authenticated:
            self.fields["counterparty_account"].queryset = LedgerAccount.objects.filter(user=user, active=True).order_by("code")
        css_class = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css_class}".strip()

    def clean_counterparty_account(self):
        account = self.cleaned_data["counterparty_account"]
        if self.user and account.user_id != self.user.id:
            raise forms.ValidationError("Choose one of your own active accounts.")
        return account
