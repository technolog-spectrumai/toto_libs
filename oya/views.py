from oya.models import DashboardBlock
from .page import PageProcessor
import os
from django.shortcuts import render, get_object_or_404
from federal.models import Federation

template_dir = "oya"


def _get_template(name):
    return os.path.join(template_dir, name)


def home_view(request):
    processor = PageProcessor()
    federation = get_object_or_404(
        Federation,
        active=True,
        platform__active=True
    )
    context = {"federation": federation}
    return render(request, _get_template("home.html"), processor.decorate(context, request))


def dashboard_view(request):
    processor = PageProcessor()

    if request.user.is_authenticated:
        dashboard_blocks = DashboardBlock.objects.all()
    else:
        dashboard_blocks = DashboardBlock.objects.filter(public=True)

    context = {
        "page_title": "Dashboard",
        "blocks": dashboard_blocks
    }

    return render(request, _get_template("dashboard.html"), processor.decorate(context, request))


def not_implemented(request):
    processor = PageProcessor()
    context = {
        "page_title": "Not Implemented"
    }
    return render(request, _get_template("placeholder.html"), processor.decorate(context, request))


def maintenance_view(request):
    processor = PageProcessor(maintenance_mode=True)
    context = {"page_title": "Under Maintenance"}
    return render(request, _get_template("maintenance.html"), processor.decorate(context, request))







