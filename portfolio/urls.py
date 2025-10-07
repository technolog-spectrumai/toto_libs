from django.urls import path
from .views import chamber_overview


app_name = "portfolio"

urlpatterns = [
    path('', chamber_overview, name='chamber_overview'),
]
