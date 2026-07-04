from django.urls import path
from django.views.generic import RedirectView
from django.urls import reverse_lazy
from django.conf import settings
from . import views

app_name = "core"

urlpatterns = [
    path("welcome/", views.welcome_view, name="welcome"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("manual/", views.manual_view, name="manual"),
    path("not-implemented/", views.not_implemented, name="not_implemented"),
    path("maintenance/", views.maintenance_view, name="maintenance"),
    path('', RedirectView.as_view(
        url=reverse_lazy('core:welcome'),
        permanent=not settings.DEBUG
    )),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
]
