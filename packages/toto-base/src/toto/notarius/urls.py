from django.urls import path

from toto.notarius.views import (
    ContractConvertPdfView,
    ContractCreateView,
    ContractEditView,
    ContractSignView,
    ContractView,
    NotariusIndexView,
    contract_save,
)

app_name = "notarius"

urlpatterns = [
    path("", NotariusIndexView.as_view(), name="index"),
    path("new/", ContractCreateView.as_view(), name="create"),
    path("view/<int:file_pk>/", ContractView.as_view(), name="view"),
    path("edit/<int:file_pk>/", ContractEditView.as_view(), name="edit"),
    path("save/<int:file_pk>/", contract_save, name="save"),
    path("sign/<int:file_pk>/", ContractSignView.as_view(), name="sign"),
    path("convert/<int:file_pk>/", ContractConvertPdfView.as_view(), name="convert_pdf"),
]
