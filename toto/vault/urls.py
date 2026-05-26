from django.urls import path
from django.views.generic import RedirectView
from .views import (
    PublicFileListView, VaultFileDownloadView,
    FileGatewayPageView, FileGatewayUploadView,
    VaultMetricsView, BucketMetricsView,
    VaultInvoiceListView,
)

app_name = "vault"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="vault:public_list", permanent=False), name="root"),
    path("public/", PublicFileListView.as_view(), name="public_list"),
    path("public/<slug:bucket_slug>/<slug:key>/", VaultFileDownloadView.as_view(), name="public_file"),
    path("gateways/dir/<int:dir_pk>/", FileGatewayPageView.as_view(), name="gateway_page"),
    path("gateways/dir/<int:dir_pk>/upload/", FileGatewayUploadView.as_view(), name="gateway_upload"),
    path("metrics/", VaultMetricsView.as_view(), name="metrics"),
    path("metrics/<slug:bucket_slug>/", BucketMetricsView.as_view(), name="bucket_metrics"),
    path("invoices/", VaultInvoiceListView.as_view(), name="invoice_list"),
]
