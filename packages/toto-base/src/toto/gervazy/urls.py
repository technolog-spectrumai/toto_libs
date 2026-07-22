from django.urls import path
from . import views

app_name = "gervazy"

urlpatterns = [
    path("my-keys/", views.my_keys, name="my_keys"),
    path("strongbox/<int:pk>/initialize/", views.initialize_strongbox_view, name="initialize_strongbox"),
    path("signing-key/provision/", views.provision_signing_key_view, name="provision_signing_key"),
]
