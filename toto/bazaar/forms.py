from django import forms
from .models import Product, ProductReview, Shipment


class AddToCartForm(forms.Form):
    product_id = forms.IntegerField(widget=forms.HiddenInput)
    variant_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    quantity = forms.IntegerField(min_value=1, initial=1)


class CheckoutForm(forms.Form):
    email = forms.EmailField()
    customer_note = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    payment_method = forms.ChoiceField(choices=[('manual','Manual'),('bank_transfer','Bank transfer'),('cash_on_delivery','Cash on delivery')])


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
