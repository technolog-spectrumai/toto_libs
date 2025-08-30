from django.urls import path
from django.views.generic import RedirectView
from django.urls import reverse_lazy
from django.conf import settings
from . import views

app_name = 'nest'

urlpatterns = [
    path("home/", views.home_view, name="home"),
    path("root/", views.root_view, name="root"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("not-implemented/", views.not_implemented, name="not_implemented"),
    path('', RedirectView.as_view(
        url=reverse_lazy('nest:home'),
        permanent=not settings.DEBUG
    )),
]
