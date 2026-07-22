from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.utils import timezone
from django.utils.html import format_html

from .models import VaultFile, Bucket, FileGateway, VaultDirectory, BucketCopyLog, StorageProvider
from toto.core.batch import BatchAction


@admin.register(StorageProvider)
class StorageProviderAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'name', 'endpoint_url_template', 'default_region',
                    'addressing_style', 'use_ssl', 'is_builtin', 'bucket_count')
    list_filter = ('addressing_style', 'use_ssl', 'is_builtin')
    search_fields = ('name', 'display_name')
    ordering = ('display_name',)
    readonly_fields = ('is_builtin',)

    fieldsets = (
        (None, {
            'fields': ('name', 'display_name', 'is_builtin'),
        }),
        ('Endpoint', {
            'fields': ('endpoint_url_template', 'default_region'),
            'description': (
                'Use {region} or {account_id} as placeholders in the endpoint URL. '
                'Leave endpoint blank for AWS (it uses default routing).'
            ),
        }),
        ('Connection defaults', {
            'fields': ('addressing_style', 'use_ssl'),
        }),
    )

    def bucket_count(self, obj):
        return obj.buckets.count()
    bucket_count.short_description = 'Buckets'

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_builtin:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Bucket)
class BucketAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'owner', 'storage_backend', 'provider',
                    'storage_quota_mb', 'connection_url_display')
    search_fields = ('name', 'owner__username')
    list_filter = ('owner', 'storage_backend', 'provider')
    ordering = ('owner', 'name')
    readonly_fields = ('connection_url_display',)
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'owner', 'storage_quota_mb'),
        }),
        ('Storage backend', {
            'fields': ('storage_backend', 'provider', 'storage_config', 'public_base_url'),
            'description': (
                'Choose a backend and, for S3, select a provider preset. '
                'Supply non-secret overrides in storage_config (bucket_name, region_name, prefix, …). '
                'For remote_toto: storage_config needs server_url + bucket_slug. '
                'Credentials must come from environment variables.'
            ),
        }),
        ('Connection URL', {
            'fields': ('connection_url_display',),
            'description': 'Shareable, credential-free URL for this bucket.',
            'classes': ('collapse',),
        }),
    )

    def connection_url_display(self, obj):
        if not obj.pk:
            return '—'
        try:
            url = obj.get_connection_url()
            return format_html(
                '<code style="user-select:all">{}</code>',
                url,
            )
        except Exception as exc:
            return f'(error: {exc})'
    connection_url_display.short_description = 'Connection URL'


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


@admin.register(BucketCopyLog)
class BucketCopyLogAdmin(admin.ModelAdmin):
    list_display = ("from_bucket", "to_bucket", "performed_by", "file_count", "performed_at")
    list_filter = ("from_bucket", "to_bucket", "performed_by")
    ordering = ("-performed_at",)
    readonly_fields = ("performed_at",)


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

