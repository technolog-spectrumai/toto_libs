from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from oya.models import DashboardBlock
from .page import PageProcessor
import os
from django.shortcuts import render
from django.http import Http404


template_dir = "oya"


def _get_template(name):
    return os.path.join(template_dir, name)


def home_view(request):
    processor = PageProcessor()
    return render(request, _get_template("home.html"), processor.decorate({}, request))


def dashboard_view(request):
    processor = PageProcessor()
    dashboard_blocks = DashboardBlock.objects.all()

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
            raise Http404("No index URL configured for active platform.")
        return redirect(f"{index_url}")
    except Http404:
        raise
    except Exception:
        raise Http404("Failed to determine platform index URL.")





