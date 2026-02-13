# vault/urls.py

from django.urls import path
from django.views.generic import RedirectView
from .views import PublicFileListView, PublicFileDownloadView

app_name = 'vault'

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='vault:public_list', permanent=False)),
    path('vault/public/', PublicFileListView.as_view(), name='public_list'),
    path('vault/public/<str:bucket>/<slug:key>/', PublicFileDownloadView.as_view(), name='public_file'),
]
