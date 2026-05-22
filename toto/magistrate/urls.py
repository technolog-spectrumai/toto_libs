from django.urls import path
from . import views

app_name = "magistrate"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("roles/", views.role_list, name="role_list"),
    path("<int:pk>/", views.magistrate_detail, name="detail"),
    path("<int:pk>/reports/new/", views.report_create, name="report_create"),
    path("reports/<int:report_pk>/acknowledge/", views.report_acknowledge, name="report_acknowledge"),
    path("communities/<slug:slug>/elect/", views.elect_propose, name="elect_propose"),
    path("proposals/<int:proposal_pk>/confirm/", views.elect_confirm, name="elect_confirm"),
]
