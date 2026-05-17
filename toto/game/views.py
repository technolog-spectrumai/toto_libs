from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from toto.ui.page import PageProcessor

from .forms import BuildingControlForm, CreatePlanetForm, CreateProvinceForm, PlanetPolicyForm, ProvinceSpecializationForm, QuickBuildForm, RenameProvinceForm
from .models import Building, Empire, GameTick, Planet, Province
from .selectors import get_planet_graph_payload, get_planet_production_summary, get_planet_resource_summary, get_province_production_summary
from .services import run_planet_tick


def render_game(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


@login_required
def command_center(request):
    empire, _ = Empire.objects.get_or_create(user=request.user, defaults={"name": f"{request.user.username}'s Empire"})
    planets = empire.planets.prefetch_related("provinces", "provinces__buildings")
    return render_game(request, "economy/player/command_center.html", {"empire": empire, "planets": planets, "game_tick": GameTick.objects.first()})


@login_required
def create_planet(request):
    empire, _ = Empire.objects.get_or_create(user=request.user, defaults={"name": f"{request.user.username}'s Empire"})
    if request.method == "POST":
        form = CreatePlanetForm(request.POST)
        if form.is_valid():
            planet = form.save(commit=False)
            planet.owner = empire
            planet.save()
            messages.success(request, "Planet created.")
            return redirect("game:planet_overview", planet_id=planet.id)
    else:
        form = CreatePlanetForm()
    return render_game(request, "economy/player/create_planet.html", {"form": form})


@login_required
def planet_overview(request, planet_id):
    empire = get_object_or_404(Empire, user=request.user)
    planet = get_object_or_404(
        Planet.objects.prefetch_related(
            "provinces__inventory__item_type",
            "provinces__buildings__building_type",
            "provinces__buildings__selected_recipe",
            "provinces__deposits__deposit_type__produces_item",
            "provinces__construction_projects__building_type",
        ),
        id=planet_id,
        owner=empire,
    )
    return render_game(request, "economy/player/planet_overview.html", {
        "planet": planet,
        "policy_form": PlanetPolicyForm(instance=planet),
        "resource_summary": get_planet_resource_summary(planet),
        "production_summary": get_planet_production_summary(planet),
        "planet_graph": get_planet_graph_payload(planet),
    })


@login_required
@require_POST
def update_planet_policy(request, planet_id):
    empire = get_object_or_404(Empire, user=request.user)
    planet = get_object_or_404(Planet, id=planet_id, owner=empire)
    form = PlanetPolicyForm(request.POST, instance=planet)
    if form.is_valid():
        form.save()
        messages.success(request, "Planet policy updated.")
    return redirect("game:planet_overview", planet_id=planet.id)


@login_required
def create_province(request, planet_id):
    empire = get_object_or_404(Empire, user=request.user)
    planet = get_object_or_404(Planet, id=planet_id, owner=empire)
    if request.method == "POST":
        form = CreateProvinceForm(request.POST)
        if form.is_valid():
            province = form.save(commit=False)
            province.planet = planet
            province.save()
            messages.success(request, "Province created.")
            return redirect("game:province_management", province_id=province.id)
    else:
        form = CreateProvinceForm()
    return render_game(request, "economy/player/create_province.html", {"planet": planet, "form": form})


@login_required
def province_management(request, province_id):
    empire = get_object_or_404(Empire, user=request.user)
    province = get_object_or_404(
        Province.objects.select_related("planet").prefetch_related(
            "inventory__item_type",
            "deposits__deposit_type__produces_item",
            "buildings__building_type",
            "buildings__selected_recipe",
            "construction_projects__building_type",
        ),
        id=province_id,
        planet__owner=empire,
    )
    return render_game(request, "economy/player/province_management.html", {
        "province": province,
        "specialization_form": ProvinceSpecializationForm(instance=province),
        "rename_form": RenameProvinceForm(instance=province),
        "quick_build_form": QuickBuildForm(province=province),
        "production_summary": get_province_production_summary(province),
    })


@login_required
@require_POST
def rename_province(request, province_id):
    empire = get_object_or_404(Empire, user=request.user)
    province = get_object_or_404(Province, id=province_id, planet__owner=empire)
    form = RenameProvinceForm(request.POST, instance=province)
    if form.is_valid():
        form.save()
        messages.success(request, "Province renamed.")
    return redirect("game:province_management", province_id=province.id)


@login_required
@require_POST
def update_province_specialization(request, province_id):
    empire = get_object_or_404(Empire, user=request.user)
    province = get_object_or_404(Province, id=province_id, planet__owner=empire)
    form = ProvinceSpecializationForm(request.POST, instance=province)
    if form.is_valid():
        form.save()
        messages.success(request, "Province role updated.")
    return redirect("game:province_management", province_id=province.id)


@login_required
@require_POST
def quick_build(request, province_id):
    empire = get_object_or_404(Empire, user=request.user)
    province = get_object_or_404(Province, id=province_id, planet__owner=empire)
    form = QuickBuildForm(request.POST, province=province)
    if form.is_valid():
        project = form.create_project()
        messages.success(request, f"Started construction: {project.building_type.name}.")
    else:
        messages.error(request, "Could not start construction. Check free cells and selected building.")
    return redirect("game:province_management", province_id=province.id)


@login_required
def building_management(request, building_id):
    empire = get_object_or_404(Empire, user=request.user)
    building = get_object_or_404(Building.objects.select_related("province__planet", "building_type", "selected_recipe"), id=building_id, province__planet__owner=empire)
    if request.method == "POST":
        form = BuildingControlForm(request.POST, instance=building)
        if form.is_valid():
            form.save()
            messages.success(request, "Building updated.")
            return redirect("game:building_management", building_id=building.id)
    else:
        form = BuildingControlForm(instance=building)
    recipe = building.selected_recipe or building.building_type.recipes.filter(is_default=True, is_active=True).first()
    output = recipe.output_quantity_per_day * building.level_multiplier * building.efficiency if recipe else None
    return render_game(request, "economy/player/building_management.html", {"building": building, "form": form, "recipe": recipe, "output": output})


@login_required
@require_POST
def player_tick_planet(request, planet_id):
    # TODO: In production, remove this or restrict to staff.
    empire = get_object_or_404(Empire, user=request.user)
    planet = get_object_or_404(Planet, id=planet_id, owner=empire)
    run_planet_tick(planet.id)
    messages.success(request, "Advanced one debug tick.")
    return redirect("game:planet_overview", planet_id=planet.id)
