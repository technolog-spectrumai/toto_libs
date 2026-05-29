from django.urls import path
from . import views

app_name = "insurance"

urlpatterns = [
    path("", views.insurance_list, name="list"),
    path("new/", views.insurance_create, name="create"),
    path("<uuid:uuid>/", views.insurance_detail, name="detail"),
    path("metrics/", views.InsuranceMetricsView.as_view(), name="metrics"),
]
