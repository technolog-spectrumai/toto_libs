from django.contrib import admin, messages
from .models import VaultPdf, VaultFile
from django.urls import path
from django.shortcuts import render, redirect
from .forms import EncryptPdfForm, EncryptFileForm, DecryptFileForm
from .models import VaultPdf
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME



@admin.register(VaultPdf)
class VaultPdfAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'is_encrypted', 'is_public', 'uploaded_at')
    list_filter = ('is_encrypted', 'is_public', 'uploaded_at')
    search_fields = ('title', 'owner__username')
    readonly_fields = ('uploaded_at',)
    actions = ['encrypt_selected_pdfs']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('encrypt/', self.admin_site.admin_view(self.encrypt_view), name='vaultpdf_encrypt'),
        ]
        return custom_urls + urls

    def encrypt_selected_pdfs(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        return redirect(f'./encrypt/?ids={",".join(selected)}')
    encrypt_selected_pdfs.short_description = "Encrypt selected PDFs with password"

    def encrypt_view(self, request):
        ids = request.GET.get('ids', '').split(',')
        queryset = VaultPdf.objects.filter(pk__in=ids)

        if request.method == 'POST':
            form = EncryptPdfForm(request.POST)
            if form.is_valid():
                user_password = form.cleaned_data['user_password']
                owner_password = form.cleaned_data['owner_password'] or user_password

                for pdf in queryset:
                    if not pdf.is_public:
                        self.message_user(request, f"Skipped {pdf.title}: not public", messages.WARNING)
                        continue
                    try:
                        pdf.encrypt_pdf(user_password=user_password, owner_password=owner_password)
                        pdf.is_public = False
                        pdf.save()
                        self.message_user(request, f"Encrypted: {pdf.title}", messages.SUCCESS)
                    except Exception as e:
                        self.message_user(request, f"Failed to encrypt {pdf.title}: {e}", messages.ERROR)
                return redirect('..')
        else:
            form = EncryptPdfForm(initial={'_selected_action': ids})

        return render(request, 'admin/encrypt_pdf.html', {
            'form': form,
            'queryset': queryset,
            'title': 'Encrypt selected PDFs',
        })


@admin.register(VaultFile)
class VaultFileAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'is_encrypted', 'is_public', 'uploaded_at')
    search_fields = ('title', 'owner__username')
    list_filter = ('is_encrypted', 'is_public', 'uploaded_at')
    readonly_fields = ('uploaded_at',)
    actions = ['encrypt_selected_files', 'decrypt_selected_files']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('encrypt/', self.admin_site.admin_view(self.encrypt_view), name='vaultfile_encrypt'),
            path(
                'decrypt/',
                self.admin_site.admin_view(self.decrypt_view),
                name='vault_vaultfile_decrypt'
            ),
        ]
        return custom_urls + urls

    def encrypt_selected_files(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        return redirect(f'./encrypt/?ids={",".join(selected)}')
    encrypt_selected_files.short_description = "Encrypt selected public files with password"

    def encrypt_view(self, request):
        ids = request.GET.get('ids', '').split(',')
        queryset = VaultFile.objects.filter(pk__in=ids)

        if request.method == 'POST':
            form = EncryptFileForm(request.POST)
            if form.is_valid():
                password = form.cleaned_data['password']

                for file in queryset:
                    if not file.is_public:
                        self.message_user(request, f"Skipped {file.title}: not public", messages.WARNING)
                        continue
                    try:
                        file.encrypt_file(password=password)
                        file.is_public = False
                        file.save()
                        self.message_user(request, f"Encrypted: {file.title}", messages.SUCCESS)
                    except Exception as e:
                        self.message_user(request, f"Failed to encrypt {file.title}: {e}", messages.ERROR)
                return redirect('..')
        else:
            form = EncryptFileForm(initial={'_selected_action': ids})

        return render(request, 'admin/encrypt_file.html', {
            'form': form,
            'queryset': queryset,
            'title': 'Encrypt selected files',
        })

    def decrypt_selected_files(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = f'./decrypt/?ids={",".join(selected)}'
        return redirect(url)
    decrypt_selected_files.short_description = "Decrypt selected encrypted files with password"

    def decrypt_view(self, request):
        ids = request.GET.get('ids', '').split(',')
        queryset = VaultFile.objects.filter(pk__in=ids)

        if request.method == 'POST':
            form = DecryptFileForm(request.POST)
            if form.is_valid():
                password = form.cleaned_data['password']

                for file in queryset:
                    if not file.is_encrypted:
                        self.message_user(request, f"Skipped {file.title}: not encrypted", messages.WARNING)
                        continue
                    try:
                        file.decrypt_file(password=password)
                        file.is_public = True
                        file.save()
                        self.message_user(request, f"Decrypted: {file.title}", messages.SUCCESS)
                    except Exception as e:
                        self.message_user(request, f"Failed to decrypt {file.title}: {e}", messages.ERROR)
                return redirect('..')
        else:
            form = DecryptFileForm(initial={'_selected_action': ids})

        return render(request, 'admin/decrypt_file.html', {
            'form': form,
            'queryset': queryset,
            'title': 'Decrypt selected files',
        })

