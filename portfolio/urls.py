from django.urls import path
from .views import fund_overview


app_name = "portfolio"

urlpatterns = [
    path('', fund_overview, name='fund-overview'),
]
