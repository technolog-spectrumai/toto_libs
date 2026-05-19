from django.urls import path

from toto.inventory import views

app_name = "inventory"

urlpatterns = [
    path("", views.inventory_dashboard, name="dashboard"),

    path("objects/", views.object_list, name="object_list"),
    path("objects/new/", views.object_create, name="object_create"),
    path("objects/<int:object_id>/", views.object_detail, name="object_detail"),
    path("objects/<int:object_id>/edit/", views.object_update, name="object_update"),

    path("object-types/", views.object_type_list, name="object_type_list"),
    path("object-types/<int:object_type_id>/edit/", views.object_type_update, name="object_type_update"),

    path("sites/", views.site_list, name="site_list"),
    path("sites/new/", views.site_create, name="site_create"),
    path("sites/<int:site_id>/", views.site_detail, name="site_detail"),
    path("sites/<int:site_id>/edit/", views.site_update, name="site_update"),
    path("sites/<int:site_id>/toggle-active/", views.site_toggle_active, name="site_toggle_active"),

    path("locations/", views.location_list, name="location_list"),
    path("locations/<int:location_id>/edit/", views.location_update, name="location_update"),
    path("locations/<int:location_id>/toggle-active/", views.location_toggle_active, name="location_toggle_active"),
]
