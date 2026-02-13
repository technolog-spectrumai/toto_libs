from django.urls import path
from django.views.generic import RedirectView
from django.urls import reverse_lazy
from django.conf import settings
from . import views

app_name = "nest"

urlpatterns = [
    path("home/", views.home_view, name="home"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("not-implemented/", views.not_implemented, name="not_implemented"),
    path("maintenance/", views.maintenance_view, name="maintenance"),
    path('', RedirectView.as_view(
        url=reverse_lazy('nest:home'),
        permanent=not settings.DEBUG
    )),

]
