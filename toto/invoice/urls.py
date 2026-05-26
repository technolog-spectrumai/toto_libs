from django.urls import path

from .views import AcceptInvoiceView, DownloadInvoiceYAMLView, InvoiceListView, InvoiceMetricsView

app_name = "invoice"

urlpatterns = [
    path("", InvoiceListView.as_view(), name="invoice_list"),
    path("metrics/", InvoiceMetricsView.as_view(), name="metrics"),
    path("<int:pk>/accept/", AcceptInvoiceView.as_view(), name="accept_invoice"),
    path("<int:pk>/yaml/", DownloadInvoiceYAMLView.as_view(), name="download_yaml"),
]
