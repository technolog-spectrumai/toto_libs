from django.urls import path
from .views import (
    NotebookListView, NotebookDetailView,
    notebook_create, notebook_update, notebook_delete,
    cell_result,
    connector_execute, connector_list,
    run_cell,
    start_kernel, stop_kernel, check_kernel, kernel_dependencies,
    notebook_vault_files,
    create_cell, delete_cell, promote_cell_to_lambda,
)
from .tpy_views import (
    TpyDisplayView, TpyIndexView, TpyCreateView, tpy_save, tpy_delete, tpy_run_cell,
    tpy_start_kernel, tpy_stop_kernel, tpy_kernel_status,
)

app_name = "mandragora"

urlpatterns = [
    path("", NotebookListView.as_view(), name="notebook_list"),
    path("new/", notebook_create, name="notebook_create"),

    # File-backed notebooks index + create (ordinary .xml vault files, <notebook> root)
    path("notebooks/",     TpyIndexView.as_view(),  name="tpy_index"),
    path("notebooks/new/", TpyCreateView.as_view(), name="tpy_create"),

    # .tpy file-backed notebook editor (wired to the vault as the "notebook" editor)
    path("tpy/<int:file_pk>/",                TpyDisplayView.as_view(), name="tpy_display"),
    path("tpy/<int:file_pk>/save/",           tpy_save,                 name="tpy_save"),
    path("tpy/<int:file_pk>/delete/",         tpy_delete,               name="tpy_delete"),
    path("tpy/<int:file_pk>/run/",            tpy_run_cell,             name="tpy_run_cell"),
    path("tpy/<int:file_pk>/kernel/start/",   tpy_start_kernel,         name="tpy_start_kernel"),
    path("tpy/<int:file_pk>/kernel/stop/",    tpy_stop_kernel,          name="tpy_stop_kernel"),
    path("tpy/<int:file_pk>/kernel/status/",  tpy_kernel_status,        name="tpy_kernel_status"),

    # Cell and kernel API endpoints (ID-based, called from JS)
    path("cells/<int:cell_id>/run/", run_cell, name="run_cell"),
    path("cells/<int:cell_id>/result/", cell_result, name="cell_result"),
    path("cells/<int:cell_id>/delete/", delete_cell, name="delete_cell"),
    path("cell/<int:cell_id>/promote/", promote_cell_to_lambda, name="promote_cell"),
    path("kernel/<int:notebook_id>/start/", start_kernel, name="start_kernel"),
    path("kernel/<int:notebook_id>/stop/", stop_kernel, name="stop_kernel"),
    path("kernel/<int:notebook_id>/status/", check_kernel, name="check_kernel"),
    path("kernel/<int:notebook_id>/dependencies/", kernel_dependencies, name="kernel_dependencies"),
    path("kernel/<int:notebook_id>/vault-files/", notebook_vault_files, name="notebook_vault_files"),
    path("connectors/", connector_list, name="connector_list"),
    path("connectors/execute/", connector_execute, name="connector_execute"),
    path("<int:notebook_id>/cells/create/", create_cell, name="create_cell"),

    # Notebook CRUD (slug-based — must come after fixed-prefix paths)
    path("<slug:slug>/edit/", notebook_update, name="notebook_update"),
    path("<slug:slug>/delete/", notebook_delete, name="notebook_delete"),
    path("<slug:slug>/", NotebookDetailView.as_view(), name="notebook_detail"),
]
