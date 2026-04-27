from django.urls import path, include
from django.views.generic import RedirectView
from django.urls import reverse_lazy
from django.conf import settings
from . import views
from .sync.api import SyncApiManager


app_name = "core"

sync_manager = SyncApiManager(settings.APPS_TO_SYNC)

urlpatterns = [
    path("home/", views.home_view, name="home"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("not-implemented/", views.not_implemented, name="not_implemented"),
    path("maintenance/", views.maintenance_view, name="maintenance"),
    path('', RedirectView.as_view(
        url=reverse_lazy('core:home'),
        permanent=not settings.DEBUG
    )),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("sync/", include(sync_manager.urlpatterns))
]
