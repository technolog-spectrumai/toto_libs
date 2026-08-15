"""datalink's url tree.

**No conditionals of any kind belong in this module.** A urls module that branches on
``apps.is_installed`` or a setting has that branch frozen at first import: swapping
``ROOT_URLCONF`` — which the two-instance harness does for every peer hop — does not
re-import a cached module, so one side would silently get the other's branch. The
serving kill switch is enforced inside the view instead, which is also the
single-chokepoint shape ``vault.storage_backends`` uses to refuse external traffic.
"""
from django.urls import path  # noqa: F401 - used as routes land

app_name = "datalink"

# The peer read API and the staff UI are added in later steps of the build.
urlpatterns: list = []
