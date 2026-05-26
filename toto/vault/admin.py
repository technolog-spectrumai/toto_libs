from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from .models import StorageAccount, VaultFile, Bucket, FileGateway, VaultDirectory
from toto.core.batch import BatchAction
from django.utils.html import format_html


@admin.register(StorageAccount)
class StorageAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "ledger_account", "name", "authorization", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("user__username", "ledger_account__code", "name")
    autocomplete_fields = ["ledger_account", "authorization"]
    readonly_fields = ("created_at", "updated_at")


@admin.register(Bucket)
class BucketAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'owner', 'tariff', 'storage_quota_mb')
    search_fields = ('name', 'owner__username')
    list_filter = ('owner',)
    ordering = ('owner', 'name')
    autocomplete_fields = ('tariff',)


@admin.register(VaultFile)
class VaultFileAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'file_type', 'is_encrypted', 'is_public',
                    'uploaded_at', 'bucket', 'directory', 'key', 'public_url_display')
    list_filter = ('file_type', 'is_encrypted', 'is_public', 'uploaded_at', 'bucket', 'directory')
    search_fields = ('title', 'owner__username')
    readonly_fields = ('uploaded_at', 'content_hash')
    actions = ['encrypt_selected_files', 'decrypt_selected_files', 'generate_content_hashes']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('encrypt/', self.admin_site.admin_view(self.encrypt_view), name='vaultfile_encrypt'),
            path('decrypt/', self.admin_site.admin_view(self.decrypt_view), name='vaultfile_decrypt'),
        ]
        return custom_urls + urls

    def public_url_display(self, obj):
        url = obj.get_public_url()
        if url:
            return format_html('<a href="{}" target="_blank">Open</a>', url)
        return "-"
    public_url_display.short_description = "Public URL"

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

    @admin.action(description="Generate content hash for selected files")
    def generate_content_hashes(self, request, queryset):
        def hash_one(file):
            if file.content_hash:
                self.message_user(request, f"Skipped {file.title} - already hashed", messages.WARNING)
                return file
            hash_value = file.create_hash()
            if hash_value:
                file.content_hash = hash_value
                file.save()
                return file
            else:
                raise ValueError(f"Failed to read file for {file.title}")

        result = BatchAction(queryset).run(hash_one)
        BatchAction.display_messages(result, self.message_user, request, verb="hash")

    generate_content_hashes.short_description = "Generate content hash for selected files"


@admin.register(FileGateway)
class FileGatewayAdmin(admin.ModelAdmin):
    list_display = ("name", "directory_path", "bucket", "make_public", "max_file_size")
    list_filter = ("bucket", "make_public")
    search_fields = ("name", "description", "directory__name")
    ordering = ("bucket", "directory__name")

    filter_horizontal = ("allowed_users",)

    fieldsets = (
        ("Gateway Info", {
            "fields": ("name", "directory", "description"),
            "description": "One gateway per directory. Bucket is auto-set from the directory.",
        }),
        ("Access Control", {
            "fields": ("allowed_users", "make_public"),
            "description": "If enabled, all uploaded files become public automatically.",
        }),
        ("Upload Limits", {
            "fields": ("max_file_size",),
            "description": "Maximum allowed file size in KB.",
        }),
    )

    def directory_path(self, obj):
        return obj.directory.full_path() if obj.directory_id else "—"
    directory_path.short_description = "Directory"


@admin.register(VaultDirectory)
class VaultDirectoryAdmin(admin.ModelAdmin):
    list_display = ("full_path_display", "bucket", "parent", "owner", "file_count", "created_at")
    list_filter = ("bucket", "owner")
    search_fields = ("name", "bucket__name", "owner__username")
    ordering = ("bucket", "parent__name", "name")
    filter_horizontal = ("allowed_users",)

    fieldsets = (
        ("Directory", {
            "fields": ("name", "bucket", "parent", "owner"),
        }),
        ("Access Control", {
            "fields": ("allowed_users",),
            "description": "Leave empty to allow all authenticated users."
        }),
    )

    def full_path_display(self, obj):
        return obj.full_path()
    full_path_display.short_description = "Path"
    full_path_display.admin_order_field = "name"

    def file_count(self, obj):
        return obj.files.count()
    file_count.short_description = "Files"
