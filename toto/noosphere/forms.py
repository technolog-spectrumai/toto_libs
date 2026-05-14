from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from .models import RemotePlatform
from .registry import get_sync_adapter
from .transport_registry import get_transport_choices


class RemotePlatformAdminForm(forms.ModelForm):
    """
    Admin form that limits transport backends to settings.NOOSPHERE_TRANSPORTS.
    """

    class Meta:
        model = RemotePlatform
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        choices = get_transport_choices()

        self.fields["uplink_backend"] = forms.ChoiceField(
            choices=choices,
            required=True,
            help_text="Transport backend for up sync. Choices come from NOOSPHERE_TRANSPORTS.",
        )

        self.fields["downlink_backend"] = forms.ChoiceField(
            choices=choices,
            required=True,
            help_text="Transport backend for down sync. Choices come from NOOSPHERE_TRANSPORTS.",
        )

    def clean_uplink_backend(self):
        value = self.cleaned_data["uplink_backend"]
        valid = dict(get_transport_choices())

        if value not in valid:
            raise ValidationError(f"Unknown Noosphere transport: {value}")

        return value

    def clean_downlink_backend(self):
        value = self.cleaned_data["downlink_backend"]
        valid = dict(get_transport_choices())

        if value not in valid:
            raise ValidationError(f"Unknown Noosphere transport: {value}")

        return value


class RemotePlatformSyncConsoleForm(forms.Form):
    """
    Admin console form for creating/updating SyncRule rows for a RemotePlatform.
    """

    models = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple,
        required=True,
        help_text="Choose models to sync with this remote platform.",
    )

    sync_creates = forms.BooleanField(
        required=False,
        initial=True,
        label="Create missing objects",
    )

    sync_updates = forms.BooleanField(
        required=False,
        initial=True,
        label="Update existing objects",
    )

    sync_deletes = forms.BooleanField(
        required=False,
        initial=False,
        label="Delete objects",
    )

    changed_since_last_sync = forms.BooleanField(
        required=False,
        initial=True,
        label="Only changed since last sync",
    )

    only_active = forms.BooleanField(
        required=False,
        initial=False,
        label="Only active objects if model has active/is_active field",
    )

    include_dependencies = forms.BooleanField(
        required=False,
        initial=False,
        label="Include dependencies",
    )

    run_after_create = forms.BooleanField(
        required=False,
        initial=False,
        label="Run sync after creating/updating rules",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        model_labels = list(getattr(settings, "NOOSPHERE_SYNCABLE_MODELS", []))

        choices = []
        for model_label in sorted(set(model_labels)):
            try:
                adapter = get_sync_adapter(model_label)
                if adapter.allowed_fields:
                    label = f"{model_label} — {len(adapter.allowed_fields)} fields"
                else:
                    label = model_label
            except LookupError:
                label = model_label
            choices.append((model_label, label))

        self.fields["models"].choices = choices
