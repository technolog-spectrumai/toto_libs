from django.shortcuts import get_object_or_404, redirect
from django.http import Http404
from vault.models import VaultFile


def public_file_view(request, bucket_name, key):
    file = get_object_or_404(
        VaultFile,
        bucket__name=bucket_name,
        key=key,
        is_public=True
    )
    if not file.file:
        raise Http404("File not found.")
    return redirect(file.file.url)
