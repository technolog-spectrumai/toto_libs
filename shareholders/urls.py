from django.urls import path
from .views import CompanyListView, CompanyDetailView


app_name = "shareholders"

urlpatterns = [
    path('companies/', CompanyListView.as_view(), name='company-list'),
    path('companies/<int:pk>/', CompanyDetailView.as_view(), name='company-detail'),
]
