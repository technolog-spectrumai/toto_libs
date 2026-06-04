from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from toto.ui.page import PageProcessor

from toto.inventory.forms import (
    InventorySiteForm,
    ObjectTypeForm,
    RealWorldObjectForm,
    StorageLocationForSiteForm,
    StorageLocationForm,
)
from toto.inventory.models import (
    InventorySite,
    ObjectType,
    RealWorldObject,
    StorageLocation,
)


def render_inventory(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


@login_required
def inventory_dashboard(request):
    objects = (
        RealWorldObject.objects
        .select_related(
            "object_type",
            "owner",
            "custodian",
            "verified_by",
            "location",
            "estimated_value_currency",
        )
        .order_by("name")[:12]
    )

    sites = (
        InventorySite.objects
        .select_related("operator", "address")
        .annotate(location_count=Count("storage_locations"))
        .order_by("name")[:12]
    )

    object_types = (
        ObjectType.objects
        .annotate(object_count=Count("typed_objects"))
        .order_by("name")[:12]
    )

    summary = {
        "objects": RealWorldObject.objects.count(),
        "object_types": ObjectType.objects.count(),
        "sites": InventorySite.objects.count(),
        "storage_locations": StorageLocation.objects.count(),
        "verified_objects": RealWorldObject.objects.exclude(verified_at__isnull=True).count(),
        "total_estimated_value": (
            RealWorldObject.objects
            .exclude(estimated_value__isnull=True)
            .aggregate(total=Sum("estimated_value"))
            .get("total")
        ),
    }

    return render_inventory(request, "inventory/dashboard.html", {
        "summary": summary,
        "objects": objects,
        "sites": sites,
        "object_types": object_types,
    })


@login_required
def object_list(request):
    query = request.GET.get("q", "").strip()
    object_type_id = request.GET.get("type", "").strip()

    objects = RealWorldObject.objects.select_related(
        "object_type",
        "owner",
        "custodian",
        "verified_by",
        "location",
        "estimated_value_currency",
    )

    if query:
        objects = objects.filter(name__icontains=query)

    if object_type_id:
        objects = objects.filter(object_type_id=object_type_id)

    objects = objects.order_by("name")

    return render_inventory(request, "inventory/object_list.html", {
        "objects": objects,
        "object_types": ObjectType.objects.order_by("name"),
        "query": query,
        "selected_object_type_id": object_type_id,
    })


@login_required
def object_detail(request, object_id):
    obj = get_object_or_404(
        RealWorldObject.objects.select_related(
            "object_type",
            "owner",
            "custodian",
            "verified_by",
            "location",
            "estimated_value_currency",
        ),
        id=object_id,
    )

    context = {
        "object": obj,
    }
    from toto.inventory.plugins.inventory_object_plugins import InventoryObjectPlugin
    context["object_plugin_sections"] = InventoryObjectPlugin.render_all(
        request=request,
        object=obj,
        inventory_object=obj,
        base_context=context,
    )

    return render_inventory(request, "inventory/object_detail.html", context)


@login_required
def object_create(request):
    if request.method == "POST":
        form = RealWorldObjectForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, "Inventory object created.")
            return redirect("inventory:object_detail", object_id=obj.id)
    else:
        form = RealWorldObjectForm()

    return render_inventory(request, "inventory/object_form.html", {
        "form": form,
        "mode": "create",
    })


@login_required
def object_update(request, object_id):
    obj = get_object_or_404(RealWorldObject, id=object_id)

    if request.method == "POST":
        form = RealWorldObjectForm(request.POST, instance=obj)
        if form.is_valid():
            obj = form.save()
            messages.success(request, "Inventory object updated.")
            return redirect("inventory:object_detail", object_id=obj.id)
    else:
        form = RealWorldObjectForm(instance=obj)

    return render_inventory(request, "inventory/object_form.html", {
        "form": form,
        "object": obj,
        "mode": "update",
    })


@login_required
def object_type_list(request):
    object_types = (
        ObjectType.objects
        .annotate(object_count=Count("typed_objects"))
        .order_by("name")
    )

    if request.method == "POST":
        form = ObjectTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Object type created.")
            return redirect("inventory:object_type_list")
    else:
        form = ObjectTypeForm()

    return render_inventory(request, "inventory/object_type_list.html", {
        "object_types": object_types,
        "form": form,
    })


@login_required
def object_type_update(request, object_type_id):
    object_type = get_object_or_404(ObjectType, id=object_type_id)

    if request.method == "POST":
        form = ObjectTypeForm(request.POST, instance=object_type)
        if form.is_valid():
            form.save()
            messages.success(request, "Object type updated.")
            return redirect("inventory:object_type_list")
    else:
        form = ObjectTypeForm(instance=object_type)

    return render_inventory(request, "inventory/object_type_form.html", {
        "form": form,
        "object_type": object_type,
    })


@login_required
def site_list(request):
    query = request.GET.get("q", "").strip()

    sites = (
        InventorySite.objects
        .select_related("operator", "address")
        .annotate(location_count=Count("storage_locations"))
    )

    if query:
        sites = sites.filter(name__icontains=query)

    sites = sites.order_by("name")

    return render_inventory(request, "inventory/site_list.html", {
        "sites": sites,
        "query": query,
    })


@login_required
def site_detail(request, site_id):
    site = get_object_or_404(
        InventorySite.objects.select_related("operator", "address"),
        id=site_id,
    )

    locations = site.storage_locations.order_by("code", "name")

    if request.method == "POST":
        form = StorageLocationForSiteForm(request.POST)
        if form.is_valid():
            location = form.save(commit=False)
            location.site = site
            location.save()
            messages.success(request, "Storage location created.")
            return redirect("inventory:site_detail", site_id=site.id)
    else:
        form = StorageLocationForSiteForm()

    return render_inventory(request, "inventory/site_detail.html", {
        "site": site,
        "locations": locations,
        "location_form": form,
    })


@login_required
def site_create(request):
    if request.method == "POST":
        form = InventorySiteForm(request.POST)
        if form.is_valid():
            site = form.save()
            messages.success(request, "Inventory site created.")
            return redirect("inventory:site_detail", site_id=site.id)
    else:
        form = InventorySiteForm()

    return render_inventory(request, "inventory/site_form.html", {
        "form": form,
        "mode": "create",
    })


@login_required
def site_update(request, site_id):
    site = get_object_or_404(InventorySite, id=site_id)

    if request.method == "POST":
        form = InventorySiteForm(request.POST, instance=site)
        if form.is_valid():
            site = form.save()
            messages.success(request, "Inventory site updated.")
            return redirect("inventory:site_detail", site_id=site.id)
    else:
        form = InventorySiteForm(instance=site)

    return render_inventory(request, "inventory/site_form.html", {
        "form": form,
        "site": site,
        "mode": "update",
    })


@login_required
def location_list(request):
    query = request.GET.get("q", "").strip()

    locations = StorageLocation.objects.select_related("site")

    if query:
        locations = locations.filter(name__icontains=query)

    locations = locations.order_by("site__name", "code", "name")

    return render_inventory(request, "inventory/location_list.html", {
        "locations": locations,
        "query": query,
    })


@login_required
def location_update(request, location_id):
    location = get_object_or_404(
        StorageLocation.objects.select_related("site"),
        id=location_id,
    )

    if request.method == "POST":
        form = StorageLocationForm(request.POST, instance=location)
        if form.is_valid():
            location = form.save()
            messages.success(request, "Storage location updated.")
            return redirect("inventory:site_detail", site_id=location.site_id)
    else:
        form = StorageLocationForm(instance=location)

    return render_inventory(request, "inventory/location_form.html", {
        "form": form,
        "location": location,
    })


@require_POST
@login_required
def site_toggle_active(request, site_id):
    site = get_object_or_404(InventorySite, id=site_id)
    site.is_active = not site.is_active
    site.save(update_fields=["is_active"])
    messages.success(request, "Inventory site status updated.")
    return redirect("inventory:site_detail", site_id=site.id)


@require_POST
@login_required
def location_toggle_active(request, location_id):
    location = get_object_or_404(StorageLocation.objects.select_related("site"), id=location_id)
    location.is_active = not location.is_active
    location.save(update_fields=["is_active"])
    messages.success(request, "Storage location status updated.")
    return redirect("inventory:site_detail", site_id=location.site_id)
