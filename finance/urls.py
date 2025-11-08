from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "finance"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="finance:account-list", permanent=False)),
    path("accounts/", views.AccountListView.as_view(), name="account-list"),
    path("accounts/<int:pk>/", views.AccountDetailView.as_view(), name="account-detail"),
]
