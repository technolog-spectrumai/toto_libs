from django.urls import path

from .views import (
    AcceptInvoiceView,
    DownloadInvoiceYAMLView,
    DownloadReportView,
    GenerateReportView,
    InvoiceListView,
    InvoiceMetricsView,
)

app_name = "invoice"

urlpatterns = [
    path("", InvoiceListView.as_view(), name="invoice_list"),
    path("metrics/", InvoiceMetricsView.as_view(), name="metrics"),
    path("<int:pk>/accept/", AcceptInvoiceView.as_view(), name="accept_invoice"),
    path("<int:pk>/yaml/", DownloadInvoiceYAMLView.as_view(), name="download_yaml"),
    path("<int:pk>/report/generate/", GenerateReportView.as_view(), name="generate_report"),
    path("reports/<int:pk>/download/", DownloadReportView.as_view(), name="download_report"),
]
