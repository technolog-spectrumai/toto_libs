from django.contrib import admin, messages
from django.urls import NoReverseMatch, path, reverse
from django.shortcuts import render, redirect
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from toto.quota.admin import QuotaPolicyAdminBase, UsageEventAdminBase

from .models import (
    VaultFile, Bucket, FileGateway, VaultDirectory, BucketCopyLog, StorageProvider,
    VaultQuotaPolicy, VaultUsageEvent,
    external_buckets_allowed,
)
from .forms import BucketPeerPairingForm
from .peering import (
    BUCKET_RIGHTS,
    BucketGrant,
    BucketPeer,
    apply_manifest,
)

# The pairing code's wire format lives in peering.py (``pairing_code_for``,
# ``decode_pairing_code``). This module mints no code since 2026-10-01: shares
# are made, and their keys rotated, in Storage → Management (share_views.py).
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

    # A preset's endpoint_url_template repoints EVERY bucket that leans on it
    # (storage_backends.get_bucket_storage fills the endpoint from the provider
    # whenever the bucket does not override it), so editing one is the same
    # authority as editing a bucket's storage backend — which admin.py already
    # reserves to superusers. has_module_permission alone is a MENU-level
    # guarantee: a staff user holding vault.change_storageprovider can still
    # reach the change view by direct URL. These four close that.
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_builtin:
            return False
        return request.user.is_superuser and super().has_delete_permission(request, obj)

    def has_module_permission(self, request):
        # Provider presets only exist to configure S3 buckets — pointless (and
        # misleading) on a local-only host.
        return (external_buckets_allowed()
                and request.user.is_superuser
                and super().has_module_permission(request))


@admin.register(Bucket)
class BucketAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'owner', 'storage_backend', 'provider',
                    'storage_quota_mb', 'ai_protected', 'created_by', 'created_at',
                    'connection_url_display')
    search_fields = ('name', 'owner__username')
    list_filter = ('owner', 'storage_backend', 'provider', 'ai_protected')
    ordering = ('owner', 'name')
    #: Who made it and when are recorded by Storage → Management and never
    #: edited; the sealed credential shows its hint only — never ciphertext.
    readonly_fields = ('connection_url_display', 'created_by', 'created_at',
                       'sealed_credential_display', 'deletion_requested_at',
                       'deletion_error', 'last_probe_at', 'last_probe_error')
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'owner', 'storage_quota_mb',
                       'ai_protected'),
        }),
        ('Record', {
            'fields': ('created_by', 'created_at', 'sealed_credential_display',
                       'last_probe_at', 'last_probe_error',
                       'deletion_requested_at', 'deletion_error'),
            'description': 'Set by Storage → Management; read-only here.',
        }),
        ('Storage backend', {
            'fields': ('storage_backend', 'provider', 'peer', 'storage_config', 'public_base_url'),
            'description': (
                'Choose a backend and, for S3, select a provider preset. '
                'Supply non-secret overrides in storage_config (bucket_name, region_name, prefix, …). '
                'For remote_toto: select the bucket peer that holds the pairing. '
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
    connection_url_display.short_description = _('Connection URL')

    def sealed_credential_display(self, obj):
        """The access key's last four characters, or how the bucket gets its
        credentials — never the ciphertext, never the key."""
        from .models import BucketSecret

        if not obj or not obj.pk:
            return '—'
        hint = BucketSecret.objects.filter(bucket_id=obj.pk).values_list('hint', flat=True).first()
        if hint is not None:
            return f'Stored key …{hint}'
        return '—'
    sealed_credential_display.short_description = _('Sealed credential')

    def get_readonly_fields(self, request, obj=None):
        """The POST-side half of the storage gate.

        ``get_fieldsets`` below only decides what is RENDERED — a hand-crafted
        POST carrying storage_backend still binds. Django drops readonly fields
        from the form's field set entirely, so listing them here makes a posted
        value ignored rather than merely unshown.
        """
        readonly = list(super().get_readonly_fields(request, obj))
        if not (external_buckets_allowed() and request.user.is_superuser):
            readonly += [f for f in ("storage_backend", "provider", "peer",
                                     "storage_config", "public_base_url")
                         if f not in readonly]
        return readonly

    def get_fieldsets(self, request, obj=None):
        # The storage-backend fieldset disappears on a local-only host AND for
        # non-superuser staff. The flag alone used to be the whole gate, which
        # let any staff member with a change-bucket permission repoint a bucket
        # at external storage; backend choice is superuser territory, like the
        # provider and peering admins below.
        fieldsets = super().get_fieldsets(request, obj)
        if external_buckets_allowed() and request.user.is_superuser:
            return fieldsets
        return tuple(fs for fs in fieldsets if fs[0] != 'Storage backend')


@admin.register(VaultFile)
class VaultFileAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'file_type', 'is_encrypted', 'is_public',
                    'uploaded_at', 'bucket', 'directory', 'key', 'public_url_display')
    list_filter = ('file_type', 'is_encrypted', 'is_public', 'uploaded_at', 'bucket', 'directory',
                   ('trashed_at', admin.EmptyFieldListFilter))
    search_fields = ('title', 'owner__username')
    readonly_fields = ('uploaded_at', 'content_hash', 'trashed_at', 'trashed_by', 'trashed_from')

    def get_queryset(self, request):
        # Every row, the trash included (2026-10-01): the default manager
        # hides trashed files, and the admin is where staff see them.
        return VaultFile.all_objects.select_related("owner", "bucket", "directory")
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
    public_url_display.short_description = _("Public URL")

    # A stored file's bytes have no address (storage.py: `.url` raises on
    # purpose), and the file input asks for one to draw its "Currently:" link,
    # so a file's change page was a 500 (2026-10-01, the 37c regression net).
    # An existing file shows its stored name instead; its bytes change through
    # the vault's own doors, which scan and record them.
    def get_exclude(self, request, obj=None):
        exclude = tuple(super().get_exclude(request, obj) or ())
        return exclude + ('file',) if obj is not None else exclude

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields + ('stored_file',) if obj is not None else fields

    @admin.display(description=_("Stored file"))
    def stored_file(self, obj):
        return obj.file.name or "-"

    def encrypt_selected_files(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse('admin:vaultfile_encrypt') + f'?ids={",".join(selected)}'
        return redirect(url)
    encrypt_selected_files.short_description = _("Encrypt selected public files with password")

    def decrypt_selected_files(self, request, queryset):
        selected = request.POST.getlist(ACTION_CHECKBOX_NAME)
        url = reverse('admin:vaultfile_decrypt') + f'?ids={",".join(selected)}'
        return redirect(url)
    decrypt_selected_files.short_description = _("Decrypt selected encrypted files with password")

    @staticmethod
    def _selected_ids(request):
        """The files the action carried in ``?ids=``, numbers only. A bare GET
        of either page (or an address edited by hand) used to hand the ORM an
        empty id and answer 500 (2026-10-01, the 37c regression net)."""
        return [part for part in request.GET.get('ids', '').split(',') if part.isdigit()]

    def encrypt_view(self, request):
        ids = self._selected_ids(request)
        queryset = VaultFile.all_objects.filter(pk__in=ids)
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
        ids = self._selected_ids(request)
        queryset = VaultFile.all_objects.filter(pk__in=ids)
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

    generate_content_hashes.short_description = _("Generate content hash for selected files")


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



@admin.register(BucketGrant)
class BucketGrantAdmin(admin.ModelAdmin):
    """Exports: "that peer may use this bucket". Superuser-only, and a record.

    Shares are made, and their keys rotated, in Storage → Management's Share
    flow (``share_views.py``), the one place a pairing code is ever shown:
    once, in the page, never stored. This admin minted codes too — on add and
    on a "Rotate api key" action — and showed them in an admin message, which
    Django's message storage may keep in a cookie; since 2026-10-01 it mints
    none (no add, no rotate) and says where shares are made instead.

    Deleting a grant is blocked — revoking is ``is_active = False``, which
    keeps the audit trail (who read what, from where, until when). Unticking
    Active, or the "Revoke the selected shares" action, is Management's Revoke
    (``share_views.revoke_share``: at once, on the audit chain); every other
    field is read-only, and a revoked share stays revoked.
    """

    list_display = ("label", "bucket", "rights_display", "is_active",
                    "expires_at", "api_key_hint", "last_read_at", "read_count")
    list_filter = ("is_active", "bucket")
    search_fields = ("label", "bucket__name", "bucket__slug")
    #: Everything but Active: whom a share is for, its bucket, its rights and
    #: its end date are set when it is made, in Management — a bucket or a
    #: right changed here would widen a code already handed over.
    readonly_fields = ("label", "bucket", "expires_at",
                       "may_list", "may_download", "may_upload", "may_delete",
                       "grant_uid", "magic_token", "api_key_hint",
                       "key_rotated_at", "created_by", "created_at",
                       "last_read_at", "read_count", "last_peer_ip")
    fieldsets = (
        (None, {
            "fields": ("label", "bucket", "is_active", "expires_at"),
            "description": _(
                "Shares are made, and their keys rotated, in Storage → "
                "Management (a bucket's Share), which shows the pairing code "
                "once. Here a share can only be revoked: untick Active and "
                "save. A revoked share stays revoked."),
        }),
        ("Capabilities", {
            "fields": ("may_list", "may_download", "may_upload", "may_delete"),
            "description": (
                "All off by default: a fresh grant authenticates and can do "
                "nothing. Copy needs no flag of its own — it is download here "
                "plus upload on the destination. The grant covers the WHOLE "
                "bucket; directory access lists do not cross hosts."
            ),
        }),
        ("Credential", {
            "fields": ("grant_uid", "magic_token", "api_key_hint",
                       "key_rotated_at"),
            "classes": ("collapse",),
        }),
        ("Audit", {
            "fields": ("created_by", "created_at", "last_read_at",
                       "read_count", "last_peer_ip"),
            "classes": ("collapse",),
        }),
    )
    actions = ["revoke_shares"]

    def rights_display(self, obj):
        granted = [r.removeprefix("may_") for r in BUCKET_RIGHTS
                   if getattr(obj, r)]
        return ", ".join(granted) or "none"
    rights_display.short_description = "Rights"

    # has_module_permission is MENU-level only: a staff user holding the model
    # permission reaches the change view by direct URL regardless. The vault's
    # "Superuser-only" contract for peering has to be an access control, not a
    # navigation hint.
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        # Making a share mints its code: Storage → Management's Share does
        # that, and shows it once in the page (2026-10-01).
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_module_permission(self, request):
        return (external_buckets_allowed()
                and request.user.is_superuser
                and super().has_module_permission(request))

    def has_delete_permission(self, request, obj=None):
        # Revoke instead: is_active=False keeps the audit trail.
        return False

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and not obj.is_active:
            # Never revived: a share's old code would work again (Management's
            # rule — make a new share instead).
            readonly.append("is_active")
        return readonly

    def changelist_view(self, request, extra_context=None):
        # The list says where shares are made, with a link
        # (admin/vault/bucketgrant/change_list.html).
        try:
            manage_url = reverse("vault:manage")
        except NoReverseMatch:
            manage_url = ""
        return super().changelist_view(
            request, {**(extra_context or {}), "manage_url": manage_url})

    def save_model(self, request, obj, form, change):
        # Active is the one field the form carries: unticked on a live share,
        # it is Management's Revoke. Nothing else is ever saved here.
        if change and not obj.is_active:
            from .share_views import revoke_share

            revoke_share(obj, request.user)

    @admin.action(description=_("Revoke the selected shares"), permissions=["change"])
    def revoke_shares(self, request, queryset):
        from .share_views import revoke_share

        revoked = sum(revoke_share(grant, request.user)
                      for grant in queryset.select_related("bucket"))
        if not revoked:
            self.message_user(request, _("Nothing to revoke: those shares were revoked already."),
                              messages.INFO)
            return
        self.message_user(request, ngettext(
            "%(count)d share revoked: the other Zenobia can no longer use its bucket.",
            "%(count)d shares revoked: the other Zenobias can no longer use their buckets.",
            revoked) % {"count": revoked}, messages.SUCCESS)


@admin.register(BucketPeer)
class BucketPeerAdmin(admin.ModelAdmin):
    """Mounts: "we use that host's bucket". Superuser-only.

    A peer with mounted buckets cannot be deleted — the FK is PROTECT, and the
    admin says so up front instead of letting the database exception surface.
    """

    list_display = ("label", "base_url", "remote_bucket_slug", "is_active",
                    "api_key_hint", "last_ok_at", "last_pull_at", "pull_count")
    list_filter = ("is_active",)
    search_fields = ("label", "base_url", "remote_bucket_slug")
    readonly_fields = ("peer_uid", "grant_uid", "magic_token", "api_key_hint",
                       "remote_bucket_slug", "capabilities", "probe_error",
                       "peer_site_name", "last_ok_at", "last_error",
                       "paired_by", "paired_at", "last_pull_at", "pull_count")

    # has_module_permission is MENU-level only: a staff user holding the model
    # permission reaches the change view by direct URL regardless. The vault's
    # "Superuser-only" contract for peering has to be an access control, not a
    # navigation hint.
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_module_permission(self, request):
        return (external_buckets_allowed()
                and request.user.is_superuser
                and super().has_module_permission(request))

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.buckets.exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = BucketPeerPairingForm
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (
                (None, {"fields": ("label", "paired_host", "base_url",
                                   "pairing_code")}),
            )
        return (
            (None, {"fields": ("label", "base_url", "is_active")}),
            ("Pairing", {
                "fields": ("peer_uid", "grant_uid", "magic_token",
                           "api_key_hint", "remote_bucket_slug",
                           "capabilities", "paired_by", "paired_at"),
                "classes": ("collapse",),
            }),
            ("Reachability", {
                "fields": ("probe_error", "peer_site_name", "last_ok_at",
                           "last_error", "last_pull_at", "pull_count"),
                "description": "Stamped by jobs and transfers, never by page "
                               "renders.",
            }),
        )

    def save_model(self, request, obj, form, change):
        if not change:
            code = form.cleaned_data["decoded_code"]
            obj.base_url = form.cleaned_data["resolved_base_url"]
            obj.grant_uid = code["grant_uid"]
            obj.magic_token = code["magic_token"]
            obj.set_api_key(code["api_key"])
            obj.remote_bucket_slug = code.get("bucket", "")
            obj.capabilities = code.get("rights", [])
            obj.paired_by = request.user
        super().save_model(request, obj, form, change)
        if not change:
            self._probe(request, obj)

    def _probe(self, request, obj):
        # One probe at pairing time, so a mangled code or an expired grant
        # surfaces now instead of during the first refresh. The client half
        # ships with the transport stage; until then pairing saves un-probed.
        try:
            from . import peer_client
        except ImportError:
            return
        try:
            manifest = peer_client.PeerClient(obj).manifest()
        except Exception as exc:
            obj.probe_error = str(exc)
            obj.save(update_fields=["probe_error"])
            self.message_user(
                request,
                f"Pairing saved, but the probe failed: {exc}. Fix the code or "
                "the grant on the exporting host, then re-pair.",
                messages.ERROR)
            return
        apply_manifest(obj, manifest)
        self.message_user(
            request,
            f"Probe OK — {obj.base_url} answered for bucket "
            f"'{obj.remote_bucket_slug}'.",
            messages.SUCCESS)


@admin.register(VaultQuotaPolicy)
class VaultQuotaPolicyAdmin(QuotaPolicyAdminBase):
    pass


@admin.register(VaultUsageEvent)
class VaultUsageEventAdmin(UsageEventAdminBase):
    pass
