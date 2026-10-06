"""Whether an editor may offer the assistant — the seam, in base.

`toto.steven` ships in **toto-ai**, which most hosts do not pin. The editors that
would offer it live in toto-base (`toto.editor`) and toto-works (`cyprian`,
`memo`, `primula`, `sketch`), neither of which may depend on that wheel. So this
is the same façade shape as `toto.vault.scanning` and `toto.quota.rates`: one
function, a lazy import inside its body, and an answer that degrades to "nothing
here" rather than raising.

    context["steven_surface"] = assistant.surface_for("cyprian")

`""` means the template renders nothing at all, so an editor carries one `{% if %}`
and no knowledge of the assistant.

Three things must be true, and an editor should not have to remember all three:

* the app is installed (it is optional, `BUILD_STEVEN_AI`);
* the surface is registered — its own `ai_surfaces.py` was discovered;
* the URLs are mounted. Installed-but-unmounted is a real state on this
  platform: zenobia keeps `toto.mandragora` in INSTALLED_APPS purely for an FK
  and serves it at no URL, which is why `toto.core.views._mounted` exists.
"""

from __future__ import annotations

#: The assistant's app. It ships in toto-ai, which most hosts do not pin.
APP = "toto.steven"


def installed() -> bool:
    """Whether this host has the assistant at all.

    The ONE question for everything outside toto-ai that exists only for the
    assistant and is not an editor's button: a bucket's AI shield (the
    checkbox in Storage → Management, its badge, the Django admin's column and
    field, the key in a bucket's audit record), the clause of "How mana works"
    that names assistant requests. A host without the app shows none of it
    and stores nothing new for it. Templates ask through
    ``{% assistant_installed as has_assistant %}`` (``app_tags``).
    """
    from django.apps import apps

    return apps.is_installed(APP)


def surface_for(key: str) -> str:
    """The surface key if the assistant can be offered here, else ""."""
    if not installed():
        return ""
    try:
        from django.urls import reverse

        from toto.core.ai_surfaces import registry

        if registry.get(key) is None:
            return ""
        reverse("steven:ask")
        reverse("steven:surface_actions", args=[key])
    except Exception:  # noqa: BLE001 - an editor must never break over this
        return ""
    return key


def allowed_for_file(vault_file) -> bool:
    """False when the file's bucket is shielded from the assistant.

    The ONE rule for ``Bucket.ai_protected``, so every door — the editor
    buttons, the file wand, the file-ask page — refuses identically and a
    bucket owner has exactly one switch to reason about. Files outside any
    bucket (``VaultFile.bucket`` is nullable) are unshielded: the shield is a
    property of the bucket, and no bucket means nobody set one.
    """
    try:
        bucket = getattr(vault_file, "bucket", None)
        return not bool(bucket and bucket.ai_protected)
    except Exception:  # noqa: BLE001 - a missing row must read as unshielded
        return True


def surface_for_file(key: str, vault_file) -> str:
    """`surface_for`, for an editor holding a vault file.

    "" when the file's bucket is AI-protected, so the editor renders no
    button, registers no handlers, and the chat chip finds no document —
    all from the same `{% if %}` the editor already carries.
    """
    if not allowed_for_file(vault_file):
        return ""
    return surface_for(key)
