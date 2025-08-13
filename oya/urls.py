from django.urls import path
from . import views

urlpatterns = [
    path("home/", views.home_view, name="home"),
    path("root/", views.root_view, name="root"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("login/", views.login_view, name="parent_login"),
    path("logout/", views.logout_view, name="parent_logout"),
    path("apply/membership/", views.membership_application_view, name="membership_application"),
    path("apply/success/<str:username>/", views.application_success_view, name="application_success"),
    path("verify/user/<str:username>/", views.verify_application_view, name="membership_verification"),
    path("verify/success/", views.verification_success_view, name="application_verified"),
    path("reference/submit/<int:application_id>/", views.reference_request_view, name="reference_request"),
    path("reference/next/<int:application_id>/", views.reference_next, name="reference_next"),
    path("profile/", views.profile_view, name="profile"),
    path("not-implemented/", views.not_implemented, name="not_implemented"),
]
