from django.urls import path
from . import views

app_name = 'community'

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("apply/membership/", views.membership_application_view, name="membership_application"),
    path("apply/success/<str:username>/", views.application_success_view, name="application_success"),
    path("verify/user/<str:username>/", views.verify_application_view, name="membership_verification"),
    path("verify/success/", views.verification_success_view, name="application_verified"),
    path("reference/submit/<int:application_id>/", views.reference_request_view, name="reference_request"),
    path("reference/next/<int:application_id>/", views.reference_next, name="reference_next"),
    path("profile/", views.profile_view, name="profile"),
    path("org-chart/", views.OrgChartView.as_view(), name="org-chart"),
    path("org-chart/data/", views.org_chart_data, name="org-chart-data"),
    path('posts/<str:username>/', views.PostListView.as_view(), name='user_posts')
]
