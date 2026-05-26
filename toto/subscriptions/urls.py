from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.plan_list, name="plan_list"),
    path("plans/new/", views.plan_create, name="plan_create"),
    path("plans/<uuid:uid>/", views.plan_detail, name="plan_detail"),
    path("plans/<uuid:uid>/edit/", views.plan_edit, name="plan_edit"),
    path("plans/<uuid:plan_uid>/prices/new/", views.price_create, name="price_create"),
    path("plans/<uuid:plan_uid>/features/new/", views.feature_create, name="feature_create"),

    path("customers/", views.customer_list, name="customer_list"),
    path("customers/new/", views.customer_create, name="customer_create"),
    path("customers/<uuid:uid>/", views.customer_detail, name="customer_detail"),

    path("subs/", views.subscription_list, name="subscription_list"),
    path("subs/new/", views.subscription_create, name="subscription_create"),
    path("subs/<uuid:uid>/", views.subscription_detail, name="subscription_detail"),
    path("subs/<uuid:uid>/cancel/", views.subscription_cancel, name="subscription_cancel"),
    path("subs/<uuid:uid>/renew/", views.subscription_renew, name="subscription_renew"),
    path("subs/<uuid:subscription_uid>/invoice/", views.invoice_create, name="invoice_create"),
    path("subs/renew-due/", views.renew_due, name="renew_due"),

    path("usage/", views.usage_list, name="usage_list"),
    path("usage/new/", views.usage_create, name="usage_create"),
    path("api/usage/", views.api_record_usage, name="api_record_usage"),

    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<uuid:uid>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<uuid:uid>/pay/", views.invoice_pay, name="invoice_pay"),

    path("metrics/", views.metrics, name="metrics"),
]
