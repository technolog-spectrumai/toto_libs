from django.urls import path

from . import views

app_name = "game"

urlpatterns = [
    path("", views.command_center, name="command_center"),
    path("planets/<int:planet_id>/", views.planet_overview, name="planet_overview"),
    path("planets/<int:planet_id>/debug-tick/", views.player_tick_planet, name="player_tick_planet"),
    path("planets/<int:planet_id>/provinces/create/", views.create_province, name="create_province"),
    path("provinces/<int:province_id>/", views.province_management, name="province_management"),
    path("provinces/<int:province_id>/rename/", views.rename_province, name="rename_province"),
    path("provinces/<int:province_id>/quick-build/", views.quick_build, name="quick_build"),
    path("buildings/<int:building_id>/", views.building_management, name="building_management"),
]
