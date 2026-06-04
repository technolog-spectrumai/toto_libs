from django.urls import path
from . import views

app_name = "magistrate"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("roles/", views.role_list, name="role_list"),
    path("<int:pk>/", views.magistrate_detail, name="detail"),
    path("<int:pk>/console/", views.magistrate_dashboard, name="my_dashboard"),
    path("<int:pk>/decisions/new/", views.make_decision, name="make_decision"),
    path("<int:pk>/reports/new/", views.report_create, name="report_create"),
    path("<int:pk>/fines/new/", views.issue_fine, name="issue_fine"),
    path("<int:pk>/freeze/", views.freeze_asset, name="freeze_asset"),
    path("freezes/<int:freeze_pk>/lift/", views.lift_freeze, name="lift_freeze"),
    path("<int:pk>/impeach/", views.impeach_propose, name="impeach_propose"),
    path("reports/<int:report_pk>/acknowledge/", views.report_acknowledge, name="report_acknowledge"),
    path("decisions/<int:decision_pk>/revoke/", views.revoke_decision, name="revoke_decision"),
    path("decisions/<int:decision_pk>/review/", views.review_decision, name="review_decision"),
    path("communities/<slug:slug>/elect/", views.elect_propose, name="elect_propose"),
    path("proposals/<int:proposal_pk>/confirm/", views.elect_confirm, name="elect_confirm"),
]
