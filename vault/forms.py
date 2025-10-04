from django import forms

class EncryptPdfForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    user_password = forms.CharField(widget=forms.PasswordInput, label="User Password")
    owner_password = forms.CharField(widget=forms.PasswordInput, required=False, label="Owner Password")


class EncryptFileForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    password = forms.CharField(widget=forms.PasswordInput, label="Encryption Password")

class DecryptFileForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    password = forms.CharField(widget=forms.PasswordInput, label="Decryption Password")

class DecryptPdfForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    password = forms.CharField(widget=forms.PasswordInput, label="Decryption Password")

