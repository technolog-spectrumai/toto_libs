"""The workspace creation form.

Ambrosia creates no buckets. You pick one you already own and then say where in
it the workspace lives — an existing folder, or a new one by name.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from toto.vault.models import VaultDirectory

from .models import WorkspaceKind
from .services import buckets_for


class WorkspaceCreateForm(forms.Form):
    name = forms.CharField(
        label=_("Workspace name"), max_length=120,
        widget=forms.TextInput(attrs={"placeholder": _("Data experiments")}),
    )
    kind = forms.ChoiceField(label=_("Kind"), choices=WorkspaceKind.choices,
                             initial=WorkspaceKind.PYTHON)
    bucket = forms.ModelChoiceField(
        label=_("Bucket"), queryset=None, empty_label=None,
        help_text=_("Which of your buckets the workspace lives in."),
    )
    directory = forms.ModelChoiceField(
        label=_("Folder"), queryset=None, required=False,
        empty_label=_("— bucket root —"),
        help_text=_("Pick the folder to use, or leave it at the root and name a "
                    "new one below."),
    )
    new_directory_name = forms.CharField(
        label=_("New folder"), max_length=200, required=False,
        widget=forms.TextInput(attrs={"placeholder": _("experiments")}),
        help_text=_("Leave empty to use the folder chosen above as it is."),
    )

    def __init__(self, *args, user=None, kind=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Under a language app's namespace the kind is the NAMESPACE's, not a
        # choice: the field disappears and the view passes the locked value to
        # create_workspace itself.
        if kind is not None:
            del self.fields["kind"]
        self.user = user
        self.fields["bucket"].queryset = buckets_for(user)
        # Every folder the user could pick, across all their buckets. The
        # template narrows this to the chosen bucket client-side; `clean()` is
        # what actually enforces the pairing, because a select is a suggestion
        # and a POST is not.
        self.fields["directory"].queryset = (
            VaultDirectory.objects.filter(bucket__owner=user)
            .select_related("bucket").order_by("bucket__name", "name")
        )

    def clean(self):
        cleaned = super().clean()
        bucket = cleaned.get("bucket")
        directory = cleaned.get("directory")
        new_name = (cleaned.get("new_directory_name") or "").strip()

        if bucket and directory and directory.bucket_id != bucket.pk:
            self.add_error("directory",
                           _("That folder is not in the bucket you chose."))
        if not directory and not new_name:
            # Adopting the whole bucket root would make the workspace's tree the
            # entire bucket and its destroy button catastrophic.
            self.add_error("new_directory_name",
                           _("Pick a folder, or give a name for a new one."))
        return cleaned


class DestroyWorkspaceForm(forms.Form):
    """Type the name to destroy the folder. Deliberately awkward."""

    confirm = forms.CharField(label=_("Type the workspace name to confirm"))

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace = workspace

    def clean_confirm(self):
        typed = (self.cleaned_data.get("confirm") or "").strip()
        if self.workspace and typed != self.workspace.name:
            raise forms.ValidationError(
                _("That does not match the workspace name."))
        return typed
