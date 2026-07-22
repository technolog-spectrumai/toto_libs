from __future__ import annotations

from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render

from toto.ui import PageProcessor


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
