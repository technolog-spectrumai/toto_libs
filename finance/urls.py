from django.urls import path
from . import views

app_name = "finance"

urlpatterns = [
    # Dashboard / List
    path("accounts/", views.account_list, name="account_list"),

    # Detail
    path("accounts/<int:pk>/", views.account_detail, name="account_detail"),
]
