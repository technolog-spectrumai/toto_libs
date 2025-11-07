from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django import forms
from vault.models import VaultFile
from yamabiko.serialize import ModelSerializer
from .batch import BatchAction


class VaultFilePickerForm(forms.Form):
    vault_file = forms.ModelChoiceField(
        queryset=VaultFile.objects.filter(file_type='json'),
        label="Select Vault File",
        required=True
    )


class BaseSerializableAdmin(admin.ModelAdmin):
    actions = ['dump_selected_objects', 'load_from_vault_action']
    #change_list_template = "admin/load_from_vault_form.html"

    def get_serializer(self):
        return ModelSerializer(self.model)

    # 📤 Dump selected objects to Vault
    def dump_selected_objects(self, request, queryset):
        owner = request.user
        filename = f"{self.model.__name__.lower()}_dump.json"

        def dump_one(obj):
            serializer = self.get_serializer()
            return serializer.dump_queryset_to_bucket(
                queryset=[obj],
                owner=owner,
                bucket=None,
                filename=filename
            )

        result = BatchAction(queryset).run(dump_one)
        BatchAction.display_messages(result, self.message_user, request, verb="dumped")

    dump_selected_objects.short_description = "Dump selected objects to Vault"

    # 📥 Load from Vault via action
    def load_from_vault_action(self, request, queryset):
        url = reverse(f"admin:{self.model._meta.model_name}_load_from_vault")
        return redirect(url)

    load_from_vault_action.short_description = "Load objects from Vault file"

    # 🔗 Custom view for loading
    def get_urls(self):
        urls = super().get_urls()
        model_name = self.model._meta.model_name
        custom_urls = [
            path(
                f"{model_name}/load-from-vault/",
                self.admin_site.admin_view(self.load_from_vault_view),
                name=f"{model_name}_load_from_vault"
            )
        ]
        return custom_urls + urls

    def load_from_vault_view(self, request):
        model_class = self.model
        if request.method == "POST":
            form = VaultFilePickerForm(request.POST)
            if form.is_valid():
                vault_file = form.cleaned_data["vault_file"]
                serializer = ModelSerializer(model_class)
                try:
                    serializer.load_from_bucket(vault_file)
                    self.message_user(request, "Data loaded successfully.")
                except ValueError as e:
                    self.message_user(request, f"Error: {str(e)}", level=messages.ERROR)
                opts = model_class._meta
                return redirect(reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"))
        else:
            form = VaultFilePickerForm()

        return render(request, "admin/load_from_vault_form.html", {
            "form": form,
            "title": f"Load {model_class.__name__} from Vault"
        })
