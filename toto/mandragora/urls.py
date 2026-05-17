from django.urls import path
from .views import (
    NotebookListView, NotebookDetailView,
    notebook_create, notebook_update, notebook_delete,
    cell_result,
    run_cell,
    start_kernel, stop_kernel, check_kernel, kernel_dependencies,
    create_cell, delete_cell, promote_cell_to_lambda,
)

app_name = "mandragora"

urlpatterns = [
    path("", NotebookListView.as_view(), name="notebook_list"),
    path("new/", notebook_create, name="notebook_create"),

    # Cell and kernel API endpoints (ID-based, called from JS)
    path("cells/<int:cell_id>/run/", run_cell, name="run_cell"),
    path("cells/<int:cell_id>/result/", cell_result, name="cell_result"),
    path("cells/<int:cell_id>/delete/", delete_cell, name="delete_cell"),
    path("cell/<int:cell_id>/promote/", promote_cell_to_lambda, name="promote_cell"),
    path("kernel/<int:notebook_id>/start/", start_kernel, name="start_kernel"),
    path("kernel/<int:notebook_id>/stop/", stop_kernel, name="stop_kernel"),
    path("kernel/<int:notebook_id>/status/", check_kernel, name="check_kernel"),
    path("kernel/<int:notebook_id>/dependencies/", kernel_dependencies, name="kernel_dependencies"),
    path("<int:notebook_id>/cells/create/", create_cell, name="create_cell"),

    # Notebook CRUD (slug-based — must come after fixed-prefix paths)
    path("<slug:slug>/edit/", notebook_update, name="notebook_update"),
    path("<slug:slug>/delete/", notebook_delete, name="notebook_delete"),
    path("<slug:slug>/", NotebookDetailView.as_view(), name="notebook_detail"),
]
