from django.urls import path
from . import views

app_name = 'gervazy'

urlpatterns = [
    path("example/", views.example_view, name="example"),
    path("tls-certificates/", views.TLSCertificateListView.as_view(), name="tls_certificate_list"),
    path("external-certificates/", views.ExternalCertificateListView.as_view(), name="external_certificate_list"),
    path("dashboard/", views.certificate_dashboard, name="certificate_dashboard"),
]
