
from .models import StaticPage, DynamicPage, Language, Image
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponse
from django.utils.safestring import mark_safe


def static_page_view(request, slug, language):
    page = get_object_or_404(StaticPage, slug=slug, language=language)
    return HttpResponse(mark_safe(page.html))


def dynamic_page_view(request, slug, lang):
    language = get_object_or_404(Language, slug=lang)
    page = get_object_or_404(DynamicPage, slug=slug, language=language)
    try:
        rendered_template = render(request, f"webfront/{page.template_key}.html", page.config_json)
    except Exception:
        raise Http404("Template rendering failed.")
    return rendered_template

def image_view(request, slug):
    image = get_object_or_404(Image, slug=slug)
    return redirect(image.image.url)
