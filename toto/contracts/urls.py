from django.urls import path

from . import views

app_name = "contracts"

urlpatterns = [
    # ---- Base contracts ----
    path("", views.contract_list, name="contract_list"),
    path("new/", views.contract_create, name="contract_create"),
    path("<uuid:uuid>/", views.contract_detail, name="contract_detail"),
    path("<uuid:uuid>/edit/", views.contract_update, name="contract_update"),
    path("<uuid:uuid>/graph.json", views.contract_graph_json, name="contract_graph_json"),
    path("<uuid:uuid>/nodes/new/", views.contract_node_create, name="contract_node_create"),
    path("<uuid:uuid>/nodes/<int:pk>/edit/", views.contract_node_update, name="contract_node_update"),
    path("<uuid:uuid>/edges/new/", views.contract_edge_create, name="contract_edge_create"),
    path("<uuid:uuid>/edges/<int:pk>/edit/", views.contract_edge_update, name="contract_edge_update"),
    # Signing
    path("<uuid:uuid>/sign/", views.contract_sign, name="contract_sign"),
    path("<uuid:uuid>/signatories/add/", views.contract_add_signatory, name="contract_add_signatory"),
    path("<uuid:uuid>/signatories/<int:pk>/remove/", views.contract_remove_signatory, name="contract_remove_signatory"),
    # Claims health
    path("<uuid:uuid>/evaluate/", views.contract_evaluate_health, name="contract_evaluate_health"),
    # Vault PDF
    path("<uuid:uuid>/pdf/attach/", views.contract_attach_pdf, name="contract_attach_pdf"),
    path("<uuid:uuid>/pdf/download/", views.contract_download_pdf, name="contract_download_pdf"),
    # Person signature
    path("person/<int:person_pk>/signature/", views.person_update_signature, name="person_update_signature"),

    # ---- Payroll ----
    path("payroll/", views.payroll_list, name="payroll_list"),
    path("payroll/new/", views.payroll_create, name="payroll_create"),
    path("payroll/<uuid:uuid>/", views.payroll_detail, name="payroll_detail"),
    path("payroll/<uuid:uuid>/duty/new/", views.payroll_duty_create, name="payroll_duty_create"),
    path("payroll/<uuid:uuid>/duty/<int:obligation_pk>/due/", views.payroll_duty_mark_due, name="payroll_duty_mark_due"),
    path("payroll/<uuid:uuid>/duty/<int:obligation_pk>/approve/", views.payroll_duty_approve, name="payroll_duty_approve"),
    path("payroll/<uuid:uuid>/duty/<int:obligation_pk>/settle/", views.payroll_duty_settle, name="payroll_duty_settle"),

    # ---- Loans ----
    path("loans/", views.loan_list, name="loan_list"),
    path("loans/new/", views.loan_create, name="loan_create"),
    path("loans/<uuid:uuid>/", views.loan_detail, name="loan_detail"),

    # ---- Insurance ----
    path("insurance/", views.insurance_list, name="insurance_list"),
    path("insurance/new/", views.insurance_create, name="insurance_create"),
    path("insurance/<uuid:uuid>/", views.insurance_detail, name="insurance_detail"),

    # ---- Leasing ----
    path("leasing/", views.lease_list, name="lease_list"),
    path("leasing/new/", views.lease_create, name="lease_create"),
    path("leasing/<uuid:uuid>/", views.lease_detail, name="lease_detail"),
]
