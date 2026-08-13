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


def surface_for(key: str) -> str:
    """The surface key if the assistant can be offered here, else ""."""
    from django.apps import apps

    if not apps.is_installed("toto.steven"):
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
