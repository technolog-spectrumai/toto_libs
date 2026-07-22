from django.urls import re_path
from .consumer import EditorFileSyncConsumer

websocket_urlpatterns = [
    re_path(r"ws/editor/file/(?P<file_pk>\d+)/$", EditorFileSyncConsumer.as_asgi()),
]
