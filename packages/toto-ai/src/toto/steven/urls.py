from django.urls import path

from . import views

app_name = "steven"

urlpatterns = [
    path("", views.console, name="console"),
    path("ask/", views.ask, name="ask"),
    path("runs/<int:pk>/", views.run_status, name="run_status"),
    path("file/<int:file_pk>/", views.file_ask, name="file_ask"),
    path("surfaces/<str:key>/", views.surface_actions, name="surface_actions"),
]
