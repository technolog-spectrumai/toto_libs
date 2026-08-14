from django.urls import path

from . import views

app_name = "steven"

urlpatterns = [
    path("", views.console, name="console"),
    path("manage/", views.manage, name="manage"),
    # Row work gets real URLs; the hub's tabs stay ?tab= — "which row" does
    # not belong in a hidden input.
    path("manage/providers/new/", views.provider_edit, name="provider_new"),
    path("manage/providers/<int:pk>/", views.provider_edit,
         name="provider_edit"),
    path("manage/providers/<int:pk>/activate/", views.provider_activate,
         name="provider_activate"),
    path("manage/providers/<int:pk>/test/", views.provider_test,
         name="provider_test"),
    path("manage/agents/new/", views.agent_edit, name="agent_new"),
    path("manage/agents/<int:pk>/", views.agent_edit, name="agent_edit"),
    path("manage/agents/<int:pk>/activate/", views.agent_activate,
         name="agent_activate"),
    path("personalization/", views.personalization, name="personalization"),
    path("ask/", views.ask, name="ask"),
    path("runs/<int:pk>/", views.run_status, name="run_status"),
    path("file/<int:file_pk>/", views.file_ask, name="file_ask"),
    path("surfaces/<str:key>/", views.surface_actions, name="surface_actions"),
]
