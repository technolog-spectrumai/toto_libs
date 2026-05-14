from django import forms

from .models import SyncRule
from .registry import get_registered_model_labels, get_sync_adapter


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

    direction = forms.ChoiceField(
        choices=SyncRule.DIRECTION_CHOICES,
        required=True,
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

        choices = []

        for model_label in get_registered_model_labels():
            try:
                adapter = get_sync_adapter(model_label)
                label = model_label
                if adapter.allowed_fields:
                    label = f"{model_label} — {len(adapter.allowed_fields)} fields"
                choices.append((model_label, label))
            except Exception:
                choices.append((model_label, model_label))

        self.fields["models"].choices = choices
