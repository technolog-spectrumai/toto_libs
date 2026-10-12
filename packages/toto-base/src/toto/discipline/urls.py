from django.urls import path

from . import views

app_name = "discipline"

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("grant/", views.grant, name="grant"),
    # Read-only lists: sigils and auras are made, changed and assigned in the
    # admin panel (admin.py).
    path("sigils/", views.sigils, name="sigils"),
    path("auras/", views.auras, name="auras"),
]
