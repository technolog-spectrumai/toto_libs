from django.urls import path
from . import views

app_name = "audit"

urlpatterns = [
    path('logs/<str:appname>/', views.view_log, name='view_log'),
]
