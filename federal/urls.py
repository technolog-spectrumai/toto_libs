# federal/urls.py
from django.urls import path
from .views import issue_token_view, verify_token_view

app_name = "federal"

urlpatterns = [
    path('token/issue/', issue_token_view, name='issue-token'),
    path('token/verify/', verify_token_view, name='verify-token'),
]
