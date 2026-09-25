from django import forms
from django.utils.translation import gettext_lazy as _

from .services import MAX_BODY


class CommentForm(forms.Form):
    body = forms.CharField(label=_("Comment"), max_length=MAX_BODY,
                           widget=forms.Textarea(attrs={"rows": 3}))
    reply_to = forms.IntegerField(required=False, widget=forms.HiddenInput)
