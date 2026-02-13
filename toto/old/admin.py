from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django import forms
from vault.models import VaultFile, Bucket
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from toto.toto.old.serialize import ModelSerializer

class VaultFilePickerForm(forms.Form):
    vault_file = forms.ModelChoiceField(
        queryset=VaultFile.objects.filter(file_type='json'),
        label="Select Vault File",
        required=True
    )

class DumpToVaultForm(forms.Form):
    bucket = forms.ModelChoiceField(
        queryset=Bucket.objects.all(),
        label="Select Bucket",
        required=True
    )
    filename = forms.CharField(
        label="Filename",
        required=True,
    )

    def __init__(self, *args, model_class=None, **kwargs):
        super().__init__(*args, **kwargs)
        if model_class:
            default_name = f"{model_class.__name__.lower()}_dump.json"
            self.fields['filename'].initial = default_name

class BaseSerializableAdmin(admin.ModelAdmin):
    actions = ['dump_selected_objects_action', 'load_from_vault_action']

    def get_serializer(self):
        return ModelSerializer(self.model)

    # 📤 Dump via action → redirect to form
    def dump_selected_objects_action(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse(f"admin:{self.model._meta.model_name}_dump_to_vault") + f"?ids={','.join(selected)}"
        return redirect(url)

    dump_selected_objects_action.short_description = "Dump selected objects to Vault"

    # 📥 Load via action → redirect to form
    def load_from_vault_action(self, request, queryset):
        url = reverse(f"admin:{self.model._meta.model_name}_load_from_vault")
        return redirect(url)

    load_from_vault_action.short_description = "Load objects from Vault file"

    # 🔗 Custom URLs
    def get_urls(self):
        model_name = self.model._meta.model_name
        custom_urls = [
            path(f"{model_name}/load-from-vault/", self.admin_site.admin_view(self.load_from_vault_view), name=f"{model_name}_load_from_vault"),
            path(f"{model_name}/dump-to-vault/", self.admin_site.admin_view(self.dump_to_vault_view), name=f"{model_name}_dump_to_vault"),
        ]
        return custom_urls + super().get_urls()

    # 📥 Load from Vault
    def load_from_vault_view(self, request):
        model_class = self.model
        form = VaultFilePickerForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            vault_file = form.cleaned_data["vault_file"]
            serializer = ModelSerializer(model_class)
            try:
                serializer.load_from_bucket(vault_file)
                self.message_user(request, "Data loaded successfully.")
            except ValueError as e:
                self.message_user(request, f"Error: {str(e)}", level=messages.ERROR)
            opts = model_class._meta
            return redirect(reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"))

        return render(request, "admin/load_from_vault_form.html", {
            "form": form,
            "title": f"Load {model_class.__name__} from Vault"
        })

    # 📤 Dump to Vault
    def dump_to_vault_view(self, request):
        model_class = self.model
        ids = request.GET.get("ids", "").split(",")
        queryset = model_class.objects.filter(pk__in=ids)

        form = DumpToVaultForm(request.POST or None, model_class=model_class)

        if request.method == "POST" and form.is_valid():
            bucket = form.cleaned_data["bucket"]
            filename = form.cleaned_data["filename"]
            owner = request.user

            serializer = ModelSerializer(model_class)
            try:
                # Dump the entire queryset in one go
                serializer.dump_queryset_to_bucket(
                    queryset=queryset,
                    owner=owner,
                    bucket=bucket,
                    filename=filename
                )
                self.message_user(request, f"{queryset.count()} objects dumped to {filename}.")
            except ValueError as e:
                self.message_user(request, f"Error: {str(e)}", level=messages.ERROR)

            opts = model_class._meta
            return redirect(reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"))

        return render(request, "admin/dump_to_vault_form.html", {
            "form": form,
            "queryset": queryset,
            "title": f"Dump {model_class.__name__} to Vault"
        })

