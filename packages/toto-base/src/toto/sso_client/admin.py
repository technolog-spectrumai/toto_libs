import json
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from toto.sso_core.manifest import ConnectionBundle
from .models import OIDCProviderConfig


class ImportBundleForm:
    pass  # we'll render a plain template with a textarea


@admin.register(OIDCProviderConfig)
class OIDCProviderConfigAdmin(admin.ModelAdmin):
    list_display = ["label", "client_id", "portal_url", "active", "imported_at"]
    list_filter = ["active"]
    readonly_fields = ["imported_at"]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "import-bundle/",
                self.admin_site.admin_view(self.import_bundle_view),
                name="sso_client_oidcproviderconfig_import_bundle",
            ),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["import_bundle_url"] = reverse("admin:sso_client_oidcproviderconfig_import_bundle")
        return super().changelist_view(request, extra_context=extra_context)

    def import_bundle_view(self, request):
        opts = self.model._meta
        context = {
            **self.admin_site.each_context(request),
            "title": "Import Connection Bundle",
            "opts": opts,
            "error": None,
            "bundle_json": "",
        }

        if request.method == "POST":
            bundle_json = request.POST.get("bundle_json", "").strip()
            context["bundle_json"] = bundle_json
            try:
                bundle = ConnectionBundle.from_json(bundle_json)
                if bundle.bundle_type != "oidc_connection_bundle":
                    raise ValueError(f"Expected bundle_type='oidc_connection_bundle', got {bundle.bundle_type!r}")
                if bundle.schema_version != 1:
                    raise ValueError(f"Unsupported schema_version: {bundle.schema_version}")

                OIDCProviderConfig.objects.filter(active=True).update(active=False)
                config = OIDCProviderConfig.objects.create(
                    label=bundle.label,
                    portal_url=bundle.portal_url,
                    client_id=bundle.client_id,
                    client_secret=bundle.client_secret,
                    scopes=bundle.scopes,
                    active=True,
                )
                self.message_user(
                    request,
                    f"Connection bundle imported: {config.label} ({config.client_id})",
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(
                    reverse("admin:sso_client_oidcproviderconfig_changelist")
                )
            except Exception as exc:
                context["error"] = str(exc)

        return render(request, "admin/sso_client/import_bundle.html", context)
