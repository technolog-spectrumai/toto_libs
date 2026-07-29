"""Video-on-demand: the vault's play button, plus a library listing.

vod owns no models (its two migrations create then drop its original tables) and
runs no tasks. It is playback only — an HTML5 <video>/<audio> element pointed at a
vault file — which is why BUILD_VOD is separable from the ffmpeg processing stack.
"""
from __future__ import annotations

from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from toto.ui import PageProcessor


def playable_file_types() -> list[str]:
    """The VaultFile types this app registers a Play plugin for.

    Read back out of the plugin registry instead of restated here, so the library
    and the vault's own Play button cannot disagree: a third plugin added to
    plugins/vault_play_plugins.py joins this list for free. Instances are filtered
    by module because the registry is shared with every other app's play plugins.
    """
    from toto.vault.plugins import VaultPlayPlugin

    return sorted({
        plugin.file_type
        for plugin in VaultPlayPlugin.registry.values()
        if type(plugin).__module__.startswith("toto.vod.") and plugin.file_type
    })


class LibraryView(View):
    """The section landing page: vault files that have a player.

    Anonymous visitors see public files only, which matches ``vault_file_play``
    below — it serves a public file without a login. Encrypted files are excluded
    for the same reason the play view refuses them: there is nothing to stream.
    """

    template_name = "vod/library.html"

    # Bound the page — the vault can hold far more media than anyone browses, and
    # there is no pagination here yet.
    LIBRARY_LIST_CAP = 300

    def get(self, request):
        from toto.vault.models import VaultFile

        file_types = playable_file_types()
        qs = (
            VaultFile.objects
            .filter(file_type__in=file_types, is_encrypted=False)
            .select_related("owner", "bucket", "directory")
        )

        if request.user.is_authenticated:
            # vault_file_play's rule, expressed in SQL: public, or yours, or living
            # in a directory you may read. The directory arm is the queryset form of
            # VaultDirectory.user_can_access, where an empty allowed_users set means
            # "everyone" — hence the isnull branch — and a superuser passes always.
            #
            # Note what this deliberately does NOT list, for either kind of user: a
            # private file with no directory at all that somebody else owns. The play
            # view has no second chance for those either (there is no directory to
            # consult), so it would 403 on every one. Not even superusers see them
            # here, because they would not be able to play them.
            if request.user.is_superuser:
                readable_dir = Q(directory__isnull=False)
            else:
                readable_dir = Q(directory__isnull=False) & (
                    Q(directory__allowed_users__isnull=True)
                    | Q(directory__allowed_users=request.user)
                )
            # The allowed_users join multiplies rows — distinct() or a shared
            # directory appears once per member.
            qs = qs.filter(
                Q(is_public=True) | Q(owner=request.user) | readable_dir
            ).distinct()
        else:
            qs = qs.filter(is_public=True)

        files = list(qs.order_by("-uploaded_at", "title")[: self.LIBRARY_LIST_CAP])

        rows = [{
            "title": f.title,
            "file_type": f.file_type,
            "owner": f.owner.username,
            "uploaded": f.uploaded_at,
            "bucket": f.bucket.name if f.bucket else "—",
            # The directory *name* only: full_path() walks parents one query at a
            # time, which would be a per-row N+1 across the whole page.
            "directory": f.directory.name if f.directory else "",
            "size_bytes": f.file_size_bytes,
            "play_url": reverse("vod:vault_file_play", args=[f.pk]),
        } for f in files]

        context = PageProcessor().decorate(
            {
                "rows": rows,
                "file_types": file_types,
                "capped": len(files) >= self.LIBRARY_LIST_CAP,
                "library_list_cap": self.LIBRARY_LIST_CAP,
            },
            request,
        )
        return render(request, self.template_name, context)


def vault_file_play(request, file_pk):
    from toto.vault.models import VaultFile

    vault_file = get_object_or_404(VaultFile, pk=file_pk)

    if vault_file.is_encrypted:
        return HttpResponseForbidden("Cannot play an encrypted file.")

    if not vault_file.is_public:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        can_access = vault_file.owner == request.user
        if not can_access and vault_file.directory:
            can_access = vault_file.directory.user_can_access(request.user)
        if not can_access:
            return HttpResponseForbidden()

    source_url = vault_file.get_public_url() or ""
    context = PageProcessor().decorate(
        {
            "vault_file": vault_file,
            "source_url": source_url,
        },
        request,
    )
    return render(request, "vod/vault_file_play.html", context)
