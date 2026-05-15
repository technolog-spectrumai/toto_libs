from django.urls import re_path
from .consumer import FileSyncConsumer

websocket_urlpatterns = [
    re_path(r"ws/texlab/file/(?P<file_id>\d+)/$", FileSyncConsumer.as_asgi()),
]
