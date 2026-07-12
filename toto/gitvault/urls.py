from django.urls import path

from . import views

app_name = "gitvault"

urlpatterns = [
    path("dir/<int:dir_pk>/init/", views.init_repo, name="init"),
    path("repo/<int:repo_pk>/status/", views.status, name="status"),
    path("repo/<int:repo_pk>/commit/", views.commit, name="commit"),
    path("repo/<int:repo_pk>/branches/", views.branches, name="branches"),
    path("repo/<int:repo_pk>/branches/create/", views.branch_create, name="branch_create"),
    path("repo/<int:repo_pk>/checkout/", views.checkout, name="checkout"),
    path("repo/<int:repo_pk>/merge/", views.merge, name="merge"),
    path("repo/<int:repo_pk>/history/", views.history_view, name="history"),
    path("repo/<int:repo_pk>/commits/<str:sha>/", views.commit_detail, name="commit_detail"),
    path("repo/<int:repo_pk>/connect/", views.connect, name="connect"),
    path("repo/<int:repo_pk>/push/", views.push, name="push"),
    path("repo/<int:repo_pk>/pull/", views.pull, name="pull"),
    path("runs/<int:run_id>/status/", views.run_status, name="run_status"),
    path("gitea/repos/", views.gitea_repos, name="gitea_repos"),
    path("file/<int:file_pk>/context/", views.file_context, name="file_context"),
]
