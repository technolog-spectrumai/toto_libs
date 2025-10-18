from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from .models import VaultFile, Bucket


@admin.register(Bucket)
class BucketAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner')
    search_fields = ('name', 'owner__username')
    list_filter = ('owner',)
    ordering = ('owner', 'name')


@admin.register(VaultFile)
class VaultFileAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'file_type', 'is_encrypted', 'is_public', 'uploaded_at', 'bucket')
    list_filter = ('file_type', 'is_encrypted', 'is_public', 'uploaded_at')
    search_fields = ('title', 'owner__username')
    readonly_fields = ('uploaded_at',)
    actions = ['encrypt_selected_files', 'decrypt_selected_files']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('encrypt/', self.admin_site.admin_view(self.encrypt_view), name='vaultfile_encrypt'),
            path('decrypt/', self.admin_site.admin_view(self.decrypt_view), name='vaultfile_decrypt'),
        ]
        return custom_urls + urls

    def encrypt_selected_files(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse('admin:vaultfile_encrypt') + f'?ids={",".join(selected)}'
        return redirect(url)
    encrypt_selected_files.short_description = "Encrypt selected public files with password"

    def decrypt_selected_files(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse('admin:vaultfile_decrypt') + f'?ids={",".join(selected)}'
        return redirect(url)
    decrypt_selected_files.short_description = "Decrypt selected encrypted files with password"

    def encrypt_view(self, request):
        ids = request.GET.get('ids', '').split(',')
        queryset = VaultFile.objects.filter(pk__in=ids)
        strategy = queryset.first().get_strategy() if queryset.exists() else None
        form = strategy.get_encrypt_form(request, ids) if strategy else None

        if request.method == 'POST' and form and form.is_valid():
            parsed = strategy.parse_encrypt_form(form)
            for file in queryset:
                if not file.is_public:
                    self.message_user(request, f"Skipped {file.title}: not public", messages.WARNING)
                    continue
                try:
                    file.encrypt(**parsed)
                    file.is_public = False
                    file.save()
                    self.message_user(request, f"Encrypted: {file.title}", messages.SUCCESS)
                except Exception as e:
                    self.message_user(request, f"Failed to encrypt {file.title}: {e}", messages.ERROR)
            return redirect('..')

        context = strategy.get_encrypt_context(queryset, form) if strategy else {
            'form': form,
            'queryset': queryset,
            'title': 'Encrypt selected files',
        }
        template = strategy.get_encrypt_template() if strategy else 'admin/encrypt_file.html'
        return render(request, template, context)

    def decrypt_view(self, request):
        ids = request.GET.get('ids', '').split(',')
        queryset = VaultFile.objects.filter(pk__in=ids)
        strategy = queryset.first().get_strategy() if queryset.exists() else None
        form = strategy.get_decrypt_form(request, ids) if strategy else None

        if request.method == 'POST' and form and form.is_valid():
            parsed = strategy.parse_decrypt_form(form)
            for file in queryset:
                if not file.is_encrypted:
                    self.message_user(request, f"Skipped {file.title}: not encrypted", messages.WARNING)
                    continue
                try:
                    file.decrypt(**parsed)
                    file.is_public = True
                    file.save()
                    self.message_user(request, f"Decrypted: {file.title}", messages.SUCCESS)
                except Exception as e:
                    self.message_user(request, f"Failed to decrypt {file.title}: {e}", messages.ERROR)
            return redirect('..')

        context = strategy.get_decrypt_context(queryset, form) if strategy else {
            'form': form,
            'queryset': queryset,
            'title': 'Decrypt selected files',
        }
        template = strategy.get_decrypt_template() if strategy else 'admin/decrypt_file.html'
        return render(request, template, context)
