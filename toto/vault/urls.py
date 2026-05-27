from django.urls import path
from django.views.generic import RedirectView
from .views import (
    PublicFileListView, VaultFileDownloadView,
    FileGatewayPageView, FileGatewayUploadView,
    VaultMetricsView, BucketMetricsView,
    CopyFilesToBucketView,
    GenerateInvoiceView,
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
    path("copy/<slug:source_slug>/", CopyFilesToBucketView.as_view(), name="copy_files"),
    path("invoices/", RedirectView.as_view(pattern_name="invoice:invoice_list", permanent=False), name="invoice_list"),
    path("invoices/generate/<slug:bucket_slug>/", GenerateInvoiceView.as_view(), name="generate_invoice"),
]
