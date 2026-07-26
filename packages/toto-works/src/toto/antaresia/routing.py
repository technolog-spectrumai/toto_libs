from django.urls import re_path
from .consumer import FileSyncConsumer

websocket_urlpatterns = [
    re_path(r"ws/antaresia/file/(?P<file_pk>\d+)/$", FileSyncConsumer.as_asgi()),
]
