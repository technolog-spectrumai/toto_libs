"""The forum's addresses. ``<slug>`` is the community's: a channel has none
of its own. Every route's view carries a ``forum_door`` mark (views.py)."""

from django.urls import path

from . import views

app_name = "forum"

urlpatterns = [
    path("", views.channel_list, name="channel_list"),
    # Before the slug: these three name no community. (A community whose
    # slug is "settings" would have no address for its channel.)
    path("settings/", views.settings_page, name="settings"),
    path("settings/save/", views.settings_save, name="settings_save"),
    path("settings/cleanup/", views.cleanup_start, name="cleanup_start"),
    path("<slug:slug>/", views.channel_detail, name="channel_detail"),
    path("<slug:slug>/feed/", views.feed, name="feed"),
    path("<slug:slug>/post/", views.post, name="post"),
    path("<slug:slug>/estimate/", views.estimate, name="estimate"),
    path("<slug:slug>/messages/<uuid:message_id>/remove/", views.message_remove,
         name="message_remove"),
    path("<slug:slug>/messages/<uuid:message_id>/image/", views.message_image,
         name="message_image"),
    path("<slug:slug>/polls/open/", views.poll_open, name="poll_open"),
    path("<slug:slug>/polls/<uuid:poll_id>/vote/", views.poll_vote, name="poll_vote"),
    path("<slug:slug>/polls/<uuid:poll_id>/close/", views.poll_close, name="poll_close"),
    path("<slug:slug>/polls/<uuid:poll_id>/remove/", views.poll_remove, name="poll_remove"),
]
