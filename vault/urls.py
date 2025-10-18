from django.urls import path
from .views import public_file_view

app_name = 'vault'

urlpatterns = [
    path('vault/public/<str:bucket_name>/<slug:key>/', public_file_view, name='public_file'),
]