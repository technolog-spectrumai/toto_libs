# forms.py
from django import forms
from .models import ImageTransform, ImageTransformParam


class ApplyTransformForm(forms.Form):
    transform = forms.ModelChoiceField(
        queryset=ImageTransform.objects.all(),
        required=True,
        label="Transform"
    )

    def __init__(self, *args, **kwargs):
        transform = kwargs.pop("transform", None)
        super().__init__(*args, **kwargs)

        # If a transform is selected, dynamically add its params
        if transform:
            for param in transform.params.all():
                self.fields[param.key] = forms.IntegerField(
                    label=f"{param.key} ({param.min_value}-{param.max_value})",
                    min_value=param.min_value,
                    max_value=param.max_value,
                    initial=param.default_value,
                    help_text=param.description,
                )
