from django import forms
from django.utils.translation import gettext_lazy as _

class EncryptPdfForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    user_password = forms.CharField(widget=forms.PasswordInput, label=_("User Password"))
    owner_password = forms.CharField(widget=forms.PasswordInput, required=False, label=_("Owner Password"))


class EncryptFileForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    password = forms.CharField(widget=forms.PasswordInput, label=_("Encryption Password"))

class DecryptFileForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    password = forms.CharField(widget=forms.PasswordInput, label=_("Decryption Password"))

class DecryptPdfForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    password = forms.CharField(widget=forms.PasswordInput, label=_("Decryption Password"))

