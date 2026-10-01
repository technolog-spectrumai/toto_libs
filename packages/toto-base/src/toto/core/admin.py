from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from toto.core.base_admin import TotoModelAdmin
from .models import Platform, Font, Theme, ColorMix, Federation


@admin.register(Font)
class FontAdmin(TotoModelAdmin):
    list_display = ('name', 'style_family', 'cdn_link')
    list_filter = ('style_family',)
    search_fields = ('name', 'cdn_link')
    ordering = ('name',)


@admin.register(ColorMix)
class ColorMixAdmin(TotoModelAdmin):
    list_display = ('name', 'preview_light', 'preview_dark')
    readonly_fields = ('preview_light', 'preview_dark')

    fieldsets = (
        (None, {'fields': ('name',)}),
        ('Light Mode Colors', {
            'fields': (
                'primary_bg_light', 'text_main_light',
                'header_bg_light', 'bubble_bg_light',
                'appbar_bg_light', 'appbar_text_light',
                'footer_bg_light', 'footer_text_light',
                'accent_light', 'warn_light',
                'success_light', 'sunken_light', 'link_light',
                'preview_light',
            )
        }),
        ('Dark Mode Colors', {
            'fields': (
                'primary_bg_dark', 'text_main_dark',
                'header_bg_dark', 'bubble_bg_dark',
                'appbar_bg_dark', 'appbar_text_dark',
                'footer_bg_dark', 'footer_text_dark',
                'accent_dark', 'warn_dark',
                'success_dark', 'sunken_dark', 'link_dark',
                'preview_dark',
            )
        }),
        ('Accent Colors', {'fields': ('accent_1', 'accent_2')}),
    )

    def preview_light(self, obj):
        return self._render_preview(
            obj.primary_bg_light, obj.bubble_bg_light,
            obj.text_main_light, obj.accent_light,
            obj.footer_bg_light, obj.footer_text_light, "Light",
        )
    preview_light.short_description = _("Light Preview")

    def preview_dark(self, obj):
        return self._render_preview(
            obj.primary_bg_dark, obj.bubble_bg_dark,
            obj.text_main_dark, obj.accent_dark,
            obj.footer_bg_dark, obj.footer_text_dark, "Dark",
        )
    preview_dark.short_description = _("Dark Preview")

    def _render_preview(self, bg, bubble, text, accent, footer_bg, footer_text, label):
        return format_html(
            '''<div style="display:flex;gap:8px;flex-wrap:wrap;">
                <div style="background-color:{bg};color:{text};padding:8px;border-radius:4px;width:120px;text-align:center;">{label} BG</div>
                <div style="background-color:{bubble};color:{text};padding:8px;border-radius:4px;width:120px;text-align:center;">Bubble</div>
                <div style="background-color:{accent};color:{text};padding:8px;border-radius:4px;width:120px;text-align:center;">Accent {label}</div>
            </div>''',
            bg=bg, bubble=bubble, text=text, accent=accent, label=label,
        )


@admin.register(Theme)
class ThemeAdmin(TotoModelAdmin):
    list_display = ('name', 'font')
    search_fields = ('name', 'font__name')
    ordering = ('name',)


@admin.register(Federation)
class FederationAdmin(TotoModelAdmin):
    list_display = ("name", "active", "created_at")
    search_fields = ("name",)
    list_filter = ("active", "created_at")


@admin.register(Platform)
# No BackupAdminMixin any more. The app-level backup engine is gone — it
# covered 32 of ~130 models and could not restore an append-only ledger
# without weakening it; what replaced it is a pg_dump + media-tar sidecar
# pair in the deploy stack (scripts/deploy.py, `backups:` block), where
# coverage is total by construction. The Backup and Seed consoles died with
# the engine; seeding is `manage.py ingress_all`, restoring is two documented
# commands in the config's DEPLOY.md.
class PlatformAdmin(TotoModelAdmin):
    list_display = (
        'site_name', 'domain', 'publication_year', 'active',
        'get_theme_name', 'rate_limit_window', 'rate_limit_max_requests',
    )
    search_fields = ('site_name', 'domain', 'theme__name')
    list_filter = ('active', 'publication_year', 'theme')
    ordering = ['publication_year']

    def get_theme_name(self, obj):
        return obj.theme.name if obj.theme else '-'
    get_theme_name.short_description = 'Theme'


def erase_command(username: str) -> str:
    """The console command that erases ``username`` (socialhub's, which a host
    sets in ``SOCIALHUB_ERASURE_COMMAND``; toto.core's own command without it)."""
    from django.apps import apps

    if apps.is_installed("toto.socialhub"):
        from toto.socialhub.erasure import command_for

        return command_for(username)
    import shlex

    return f"python manage.py erase_user {shlex.quote(username)}"


class ConsoleErasedUserAdmin(UserAdmin):
    """Django's user administration without its delete (2026-10-01, 37c.21).

    A delete here skipped ``erase_user``: the account went by the bare
    cascade, so the profile picture's file, the version bodies, the forum's
    pictures and recordings, the membership application and the home pin
    stayed, the name stayed on messages, bucket and ledger account, and no
    erasure request was closed or recorded. The privacy notice says what an
    erase does, so the console is the one way to it: no delete action, no
    delete page, no button — and the pages say where to go instead.
    """

    change_form_template = "admin/toto_core/user_change_form.html"
    change_list_template = "admin/toto_core/user_change_list.html"

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, unquote(object_id))
        username = obj.get_username() if obj is not None else "USERNAME"
        return super().change_view(request, object_id, form_url, {
            **(extra_context or {}), "erase_command": erase_command(username)})

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {
            **(extra_context or {}), "erase_command": erase_command("USERNAME")})


_User = get_user_model()
if admin.site.is_registered(_User) and type(admin.site._registry[_User]) is UserAdmin:
    admin.site.unregister(_User)
    admin.site.register(_User, ConsoleErasedUserAdmin)
