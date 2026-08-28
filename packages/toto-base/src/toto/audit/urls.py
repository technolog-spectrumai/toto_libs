from django.urls import path

from toto.audit import views

app_name = "audit"

urlpatterns = [
    path("", views.index, name="index"),
    path("verify/", views.verify, name="verify"),
    path("<uuid:pk>/", views.detail, name="detail"),
]
