import json
from datetime import timedelta

from django.contrib import messages
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor
from toto.socialhub.models import Person
from toto.locations.models import Address
from toto.locations.views import geometry_json, route_payload, travel_payload

from .models import Travel, Visit
from .forms import TravelForm, VisitForm


def current_person(request):
    if not request.user.is_authenticated:
        return None

    person = getattr(request.user, "community_profile", None)

    if person:
        return person

    return Person.objects.filter(user=request.user).first()


@login_required
def my_travels(request):
    person = current_person(request)

    travels = (
        Travel.objects
        .select_related("route", "route__start_address", "route__end_address")
        .prefetch_related("participants")
        .order_by("-starts_at")
    )

    if person:
        my_travel_list = travels.filter(participants=person)
    else:
        my_travel_list = travels.none()

    visits = (
        Visit.objects
        .select_related("participant", "location")
        .order_by("-visited_at")
    )

    context = {
        "travels": travels,
        "my_travels": my_travel_list,
        "person": person,
        "visits": visits,
    }

    return render(
        request,
        "travels/my_travels.html",
        PageProcessor().decorate(context, request),
    )


@login_required
def travel_review(request, pk):
    travel = get_object_or_404(
        Travel.objects
        .select_related(
            "route",
            "route__start_address",
            "route__end_address",
            "route__route_chain",
        )
        .prefetch_related("participants"),
        pk=pk,
    )

    route = travel.route
    review_locations = []

    if route:
        review_locations = [
            location
            for location in (route.start_address, route.end_address)
            if location
        ]

    address_ids = [loc.pk for loc in review_locations if loc]
    location_reviews = (
        Visit.objects
        .filter(location_id__in=address_ids)
        .select_related("participant", "location")
        .order_by("-id")
    )

    context = {
        "travel": travel,
        "route": route,
        "travel_payload_json": json.dumps(travel_payload(travel)),
        "review_locations": review_locations,
        "reviews": location_reviews,
        "person": current_person(request),
    }

    return render(
        request,
        "travels/travel_review.html",
        PageProcessor().decorate(context, request),
    )


@login_required
def visit_review(request, address_id):
    location = get_object_or_404(Address, pk=address_id)

    reviews = (
        Visit.objects
        .filter(location=location)
        .select_related("participant", "location")
        .order_by("-visited_at", "-reviewed_at", "-id")
    )

    average_score = (
        reviews
        .filter(score__isnull=False)
        .aggregate(value=Avg("score"))["value"]
    )

    if average_score is not None:
        average_score = round(average_score, 1)

    score_counts = {
        item["score"]: item["count"]
        for item in (
            reviews
            .filter(score__isnull=False)
            .values("score")
            .annotate(count=Count("id"))
        )
    }

    max_count = max(score_counts.values(), default=0)

    score_distribution = []

    for score in range(5, 0, -1):
        count = score_counts.get(score, 0)

        score_distribution.append({
            "score": score,
            "count": count,
            "percent": round((count / max_count) * 100, 1) if max_count else 0,
        })

    context = {
        "address": location,
        "location": location,
        "location_payload_json": json.dumps({
            "id": location.pk,
            "name": str(location),
            "detail": location.locality_name,
            "geometry": geometry_json(location.geometry),
        }),
        "reviews": reviews,
        "average_score": average_score,
        "score_distribution": score_distribution,
        "person": current_person(request),
    }

    return render(
        request,
        "travels/visit_review.html",
        PageProcessor().decorate(context, request),
    )


@require_POST
@login_required
def submit_visit_review(request, address_id):
    person = current_person(request)
    fallback_url = reverse("locations:locations_all")
    next_url = request.POST.get("next") or fallback_url

    if not person:
        messages.error(
            request,
            "You need a community profile before you can submit a visit review.",
        )
        return redirect(next_url)

    location = get_object_or_404(Address, pk=address_id)

    review = request.POST.get("review", "").strip()
    raw_score = request.POST.get("score")

    score = None

    if raw_score not in (None, ""):
        try:
            score = int(raw_score)
        except ValueError:
            messages.error(request, "Score must be a number from 1 to 5.")
            return redirect(next_url)

        if score < 1 or score > 5:
            messages.error(request, "Score must be from 1 to 5.")
            return redirect(next_url)

    Visit.objects.update_or_create(
        participant=person,
        location=location,
        defaults={
            "review": review,
            "score": score,
        },
    )

    messages.success(request, "Visit review saved.")
    return redirect(next_url)


@require_POST
@login_required
def update_travel_info(request, pk):
    travel = get_object_or_404(Travel, pk=pk)

    travel.info = request.POST.get("info", "").strip()
    travel.save(update_fields=["info"])

    messages.success(request, "Travel info saved.")
    return redirect("travels:travel_review", pk=travel.pk)


@login_required
def travel_create(request):
    from toto.locations.models import Route
    from toto.locations.views import route_payload

    route_id = request.GET.get("route")
    now = timezone.now()
    initial = {
        "start_date": now.date(),
        "start_time": now.strftime("%H:%M"),
        "end_date": now.date(),
        "end_time": now.strftime("%H:%M"),
    }
    if route_id:
        initial["route"] = route_id

    if request.method == "POST":
        form = TravelForm(request.POST)

        if form.is_valid():
            travel = form.save()
            messages.success(request, "Travel created.")

            if travel.route:
                return redirect("locations:route_detail", pk=travel.route.pk)

            return redirect("travels:travel_review", pk=travel.pk)

    else:
        form = TravelForm(initial=initial)

    routes = Route.objects.select_related("start_address", "end_address", "route_chain")
    routes_json = json.dumps([route_payload(r) for r in routes])

    context = {
        "form": form,
        "routes_json": routes_json,
        "initial_route_id": int(route_id) if route_id and route_id.isdigit() else None,
    }

    return render(
        request,
        "travels/travel_form.html",
        PageProcessor().decorate(context, request),
    )


@login_required
def travel_metrics(request):
    from .metrics import TravelMetricsCalculator

    chart_colors = ["#4f5fa1", "#2f3d63", "#5fa38c", "#4a8f7a", "#ff4455", "#d94a4a"]
    success_color = "#5fa38c"
    accent_color = "#4f5fa1"

    calculator = TravelMetricsCalculator()
    metrics = calculator.get_context_data()

    route_items = metrics["route_items"]
    destination_items = metrics["destination_items"]
    person_items = metrics["person_items"]

    def bar_chart(labels, datasets, stacked=False, max_y=None):
        y_opts = {"beginAtZero": True}
        if max_y is not None:
            y_opts["max"] = max_y
        return {
            "chart_type": "bar",
            "labels": labels,
            "datasets": datasets,
            "options": {
                "scales": {
                    "x": {"stacked": stacked},
                    "y": {**y_opts, "stacked": stacked},
                }
            },
        }

    travels_by_route_chart = bar_chart(
        labels=[item["name"] for item in route_items],
        datasets=[{
            "label": "Travels",
            "data": [item["travel_count"] for item in route_items],
            "backgroundColor": chart_colors[: len(route_items)],
        }],
    )

    visits_by_destination_chart = bar_chart(
        labels=[item["name"] for item in destination_items[:8]],
        datasets=[{
            "label": "Visits",
            "data": [item["visit_count"] for item in destination_items[:8]],
            "backgroundColor": success_color,
        }],
    )

    travel_score_chart = bar_chart(
        labels=["5 ★", "4 ★", "3 ★", "2 ★", "1 ★"],
        datasets=[{
            "label": "Travels",
            "data": [item["count"] for item in metrics["travel_score_distribution"]],
            "backgroundColor": accent_color,
        }],
    )

    visit_score_chart = bar_chart(
        labels=["5 ★", "4 ★", "3 ★", "2 ★", "1 ★"],
        datasets=[{
            "label": "Visits",
            "data": [item["count"] for item in metrics["visit_score_distribution"]],
            "backgroundColor": success_color,
        }],
    )

    person_chart = bar_chart(
        labels=[item["name"] for item in person_items],
        datasets=[
            {
                "label": "Travels",
                "data": [item["travel_count"] for item in person_items],
                "backgroundColor": accent_color,
            },
            {
                "label": "Visits",
                "data": [item["visit_count"] for item in person_items],
                "backgroundColor": success_color,
            },
        ],
        stacked=True,
    )

    context = {
        **metrics,
        "travels_by_route_chart_json": json.dumps(travels_by_route_chart),
        "visits_by_destination_chart_json": json.dumps(visits_by_destination_chart),
        "travel_score_chart_json": json.dumps(travel_score_chart),
        "visit_score_chart_json": json.dumps(visit_score_chart),
        "person_chart_json": json.dumps(person_chart),
    }

    return render(
        request,
        "travels/travel_metrics.html",
        PageProcessor().decorate(context, request),
    )


@login_required
def visit_create(request):
    from toto.locations.views import geometry_json

    person = current_person(request)
    address_id = request.GET.get("address")

    now = timezone.now()
    tomorrow = now + timedelta(hours=24)
    initial = {
        "visited_date": now.date(),
        "visited_time": now.strftime("%H:%M"),
        "ended_date": tomorrow.date(),
        "ended_time": tomorrow.strftime("%H:%M"),
    }
    if address_id:
        initial["location"] = address_id
    if person:
        initial["participant"] = person

    if request.method == "POST":
        form = VisitForm(request.POST)

        if form.is_valid():
            visit = form.save()
            messages.success(request, "Visit saved.")

            if visit.location:
                return redirect("travels:visit_review", address_id=visit.location.pk)

            return redirect("travels:my_travels")

    else:
        form = VisitForm(initial=initial)

    addresses = Address.objects.all()
    addresses_json = json.dumps([
        {
            "id": addr.pk,
            "name": str(addr),
            "locality": addr.locality_name,
            "country": addr.country_name,
            "geometry": geometry_json(addr.geometry),
        }
        for addr in addresses
        if addr.geometry
    ])

    context = {
        "form": form,
        "addresses_json": addresses_json,
        "initial_address_id": int(address_id) if address_id and address_id.isdigit() else None,
    }

    return render(
        request,
        "travels/visit_form.html",
        PageProcessor().decorate(context, request),
    )


@require_POST
@login_required
def travel_delete(request, pk):
    travel = get_object_or_404(Travel, pk=pk)
    travel.delete()
    messages.success(request, "Travel deleted.")
    return redirect("travels:my_travels")


@require_POST
@login_required
def visit_delete(request, pk):
    visit = get_object_or_404(Visit, pk=pk)
    address_id = visit.location_id
    visit.delete()
    messages.success(request, "Visit deleted.")
    if address_id:
        return redirect("travels:visit_review", address_id=address_id)
    return redirect("travels:my_travels")
