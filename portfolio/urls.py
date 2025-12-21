from django.urls import path
from . import views

app_name = "portfolio"

urlpatterns = [
    path("", views.company_list, name="company_list"),          # /companies/
    path("<uuid:pk>/", views.company_detail, name="company_detail"),  # /companies/5/
]
