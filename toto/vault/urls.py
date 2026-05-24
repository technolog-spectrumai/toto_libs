from django.urls import path
from django.views.generic import RedirectView
from .views import PublicFileListView, VaultFileDownloadView, FileGatewayPageView, FileGatewayUploadView, VaultMetricsView

app_name = "vault"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="vault:public_list", permanent=False), name="root"),
    path("public/", PublicFileListView.as_view(), name="public_list"),
    path("public/<slug:bucket_slug>/<slug:key>/", VaultFileDownloadView.as_view(), name="public_file"),
    path("gateways/<slug:bucket_slug>/", FileGatewayPageView.as_view(), name="gateway_page"),
    path("gateways/<slug:bucket_slug>/upload/", FileGatewayUploadView.as_view(), name="gateway_upload"),
    path("metrics/", VaultMetricsView.as_view(), name="metrics"),
]