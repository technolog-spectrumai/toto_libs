from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("", views.index, name="index"),
    path("<uuid:uid>/", views.detail, name="detail"),
    path("<uuid:uid>/verify/", views.verify, name="verify"),
    path("<uuid:uid>/export.xml", views.export_xml, name="export_xml"),
    path("<uuid:uid>/export.zip", views.export_zip, name="export_zip"),
    path("<uuid:uid>/graph.json", views.graph, name="graph"),
    path("<uuid:uid>/blocks/<int:sequence>/", views.block, name="block"),
    path("<uuid:uid>/checkpoints/", views.checkpoints, name="checkpoints"),
    path("<uuid:uid>/checkpoints/take/", views.checkpoint_take, name="checkpoint_take"),
    path("<uuid:uid>/checkpoints/verify/", views.checkpoint_verify, name="checkpoint_verify"),
    path("<uuid:uid>/export.pdf", views.export_pdf, name="export_pdf"),
]
