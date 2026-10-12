from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from toto.core.base_admin import TotoModelAdmin
from .models import Platform, Font, Federation


@admin.register(Font)
class FontAdmin(TotoModelAdmin):
    list_display = ('name', 'style_family', 'cdn_link')
    list_filter = ('style_family',)
    search_fields = ('name', 'cdn_link')
    ordering = ('name',)


# No ColorMix and no Theme admin since 2026-10-10: the colours are one file,
# toto/core/palette.json, compiled into the stylesheet when the image is
# built. A platform's font is still chosen here, on the Platform.


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
        'font', 'rate_limit_window', 'rate_limit_max_requests',
    )
    search_fields = ('site_name', 'domain', 'font__name')
    list_filter = ('active', 'publication_year', 'font')
    ordering = ['publication_year']


def erase_command(username: str) -> str:
    """The console command that erases ``username`` (socialhub's, which a host
    sets in ``SOCIALHUB_ERASURE_COMMAND``; toto.core's own command without it)."""
    from django.apps import apps

    if apps.is_installed("toto.socialhub"):
        from toto.socialhub.erasure import command_for

        return command_for(username)
    import shlex

    return f"python manage.py erase_user {shlex.quote(username)}"


#: What the user list shows for making an account when the host names no
#: wrapper of its own: core's ``bootstrap_users``, which reads the account
#: as JSON on stdin, so the password is never in argv.
DEFAULT_CREATE_COMMAND = "python manage.py bootstrap_users"


def create_command(username: str = "USERNAME") -> str:
    """The console command that makes an account (2026-10-01, 37c.32): the
    host's wrapper where ``ACCOUNT_CREATE_COMMAND`` names one (zenobia's
    ``tools/create_user.py``; ``{username}`` shell-quoted), core's own
    ``bootstrap_users`` without it."""
    import shlex

    from django.conf import settings

    template = getattr(settings, "ACCOUNT_CREATE_COMMAND", "") or DEFAULT_CREATE_COMMAND
    return template.format(username=shlex.quote(username))


class ConsoleErasedUserAdmin(UserAdmin):
    """Django's user administration without its delete (2026-10-01, 37c.21)
    and, since 37c.32 the same day, without its add.

    A delete here skipped ``erase_user``: the account went by the bare
    cascade, so the profile picture's file, the version bodies, the forum's
    pictures and recordings, the membership application and the home pin
    stayed, the name stayed on messages, bucket and ledger account, and no
    erasure request was closed or recorded. The privacy notice says what an
    erase does, so the console is the one way to it: no delete action, no
    delete page, no button — and the pages say where to go instead.

    Making an account is the console's too — the owner's rule is that
    accounts are created and deleted there only. The add page made one with
    a password typed into a web form and every flag at hand, so anybody
    holding an administrator's session could mint a superuser: no add page,
    no button, and the list names the command (``create_command``).
    """

    change_form_template = "admin/toto_core/user_change_form.html"
    change_list_template = "admin/toto_core/user_change_list.html"

    def has_add_permission(self, request):
        return False

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
            **(extra_context or {}), "erase_command": erase_command("USERNAME"),
            "create_command": create_command()})


_User = get_user_model()
if admin.site.is_registered(_User) and type(admin.site._registry[_User]) is UserAdmin:
    admin.site.unregister(_User)
    admin.site.register(_User, ConsoleErasedUserAdmin)
