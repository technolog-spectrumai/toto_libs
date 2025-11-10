from django.urls import path
from . import views

app_name = 'gate'

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("challenge/", views.challenge_identity_view, name="challenge_identity"),
    path("challenge/verify/", views.challenge_signature_view, name="challenge_signature")
]
