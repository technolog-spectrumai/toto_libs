from django import forms
from .models import Product, ProductReview, Shipment

_INPUT = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20"
)
_XBIND = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
)


def _text(placeholder=""):
    return forms.TextInput(attrs={"placeholder": placeholder, "class": _INPUT, "x-bind:class": _XBIND})


class AddToCartForm(forms.Form):
    product_id = forms.IntegerField(widget=forms.HiddenInput)
    variant_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    quantity = forms.IntegerField(min_value=1, initial=1)


class CheckoutForm(forms.Form):
    customer_note = forms.CharField(
        required=False,
        label='Delivery note',
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Any special instructions for delivery…',
            'class': _INPUT,
            'x-bind:class': _XBIND,
        }),
    )

    # Saved ShipmentLocation PK — set when user picks a saved location card
    selected_location = forms.IntegerField(required=False, widget=forms.HiddenInput)
    # Profile address PK fallback
    shipping_address = forms.IntegerField(required=False, widget=forms.HiddenInput)

    # New address fields — all optional; if any are filled a new Address is created
    new_country   = forms.CharField(required=False, label='Country', widget=_text('PL'))
    new_state     = forms.CharField(required=False, label='Region / state', widget=_text('Region / state'))
    new_locality  = forms.CharField(required=False, label='City / town', widget=_text('City / town'))
    new_street    = forms.CharField(required=False, label='Street', widget=_text('Street'))
    new_building  = forms.CharField(required=False, label='Building', widget=_text('Building No.'))
    new_apartment = forms.CharField(required=False, label='Apartment', widget=_text('Apartment (optional)'))
    new_latitude  = forms.FloatField(required=False, widget=forms.HiddenInput)
    new_longitude = forms.FloatField(required=False, widget=forms.HiddenInput)
    save_location = forms.BooleanField(required=False, label='Save this address for later')
    location_label = forms.CharField(required=False, label='Label', widget=_text('Home, Work, Mum\'s…'))

    payment_method = forms.ChoiceField(choices=[
        ('transfer', 'Transfer (asset balance)'),
        ('on_delivery', 'On Delivery (pay later)'),
        ('bank_transfer', 'Bank transfer'),
        ('manual', 'Manual'),
    ])

    def has_new_address(self):
        d = self.cleaned_data
        return any(d.get(f) for f in ('new_locality', 'new_street', 'new_latitude'))


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['shop','vendor','category','name','summary','description','product_type','status','price','compare_at_price','currency','stock_tracking_enabled','stock_quantity','origin_address','pickup_address','pickup_available','delivery_zones','event','asset','is_featured','is_public','metadata']
        widgets = {'description': forms.Textarea(attrs={'rows': 5}), 'summary': forms.Textarea(attrs={'rows': 2})}


class ReviewForm(forms.ModelForm):
    class Meta:
        model = ProductReview
        fields = ['rating', 'title', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 4})}


class ShipmentForm(forms.ModelForm):
    class Meta:
        model = Shipment
        fields = ['shipping_method','origin_address','destination_address','tracking_number','carrier','status','metadata']
