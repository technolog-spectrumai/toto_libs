from django.shortcuts import redirect
from oya.models import DashboardBlock
from .page import PageProcessor
import os
from django.shortcuts import render
from django.http import Http404
from .apps import OyaConfig
from django.urls import reverse


template_dir = "oya"


def _get_template(name):
    return os.path.join(template_dir, name)


def home_view(request):
    processor = PageProcessor()
    return render(request, _get_template("home.html"), processor.decorate({}, request))


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


def root_view(request):
    try:
        processor = PageProcessor()
        index_url = processor.config.index_url
        if not index_url:
            url_app_name = OyaConfig.url_name
            return redirect(reverse(f"{url_app_name}:home"))
        return redirect(f"{index_url}")
    except Http404:
        raise
    except Exception:
        raise Http404("Failed to determine platform index URL.")


# def maintenance_view(request):
#     processor = PageProcessor()
#     context = {"page_title": "Under Maintenance"}
#     return render(request, _get_template("maintenance.html"), processor.decorate(context, request))







