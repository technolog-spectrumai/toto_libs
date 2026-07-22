"""Shared vault-file edit-guard helpers.

Encrypted vault files hold ciphertext, so they must never open in an editor. The
per-app editor views call :func:`encrypted_lock_response` at their entry point to
return a friendly "decrypt it first" page (HTTP 403) instead of the editor, mirroring
how the vault UI already hides the Edit button for encrypted files.
"""

from __future__ import annotations

from django.shortcuts import render


def encrypted_lock_response(request, vault_file=None):
    """Render the 'file is encrypted — decrypt it first' page with HTTP 403.

    Used as the entry guard in every editor view. Returns a full HttpResponse so a
    view can ``return encrypted_lock_response(request, vf)`` directly.
    """
    from toto.ui import PageProcessor

    context = PageProcessor().decorate(
        {
            "vault_file": vault_file,
            "vault_url": _vault_url(),
        },
        request,
    )
    return render(request, "vault/encrypted_locked.html", context, status=403)


def _vault_url() -> str:
    from django.urls import reverse, NoReverseMatch

    try:
        return reverse("vault:public_list")
    except NoReverseMatch:
        return "/"
