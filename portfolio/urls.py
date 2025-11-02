from django.urls import path
from .views import fund_overview, CompanyDetailView


app_name = "portfolio"

urlpatterns = [
    path('', fund_overview, name='fund_overview'),
    path('company/<int:pk>/', CompanyDetailView.as_view(), name='company-detail'),
]
