from django.urls import path
from . import views

app_name = "payroll"

urlpatterns = [
    path("", views.payroll_list, name="list"),
    path("new/", views.payroll_create, name="create"),
    path("<uuid:uuid>/", views.payroll_detail, name="detail"),
    path("<uuid:uuid>/duty/new/", views.payroll_duty_create, name="duty_create"),
    path("<uuid:uuid>/duty/<int:obligation_pk>/due/", views.payroll_duty_mark_due, name="duty_mark_due"),
    path("<uuid:uuid>/duty/<int:obligation_pk>/approve/", views.payroll_duty_approve, name="duty_approve"),
    path("<uuid:uuid>/duty/<int:obligation_pk>/settle/", views.payroll_duty_settle, name="duty_settle"),
    path("metrics/", views.PayrollMetricsView.as_view(), name="metrics"),
]
