from .models import StaticPage, DynamicPage, Language, Image
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.safestring import mark_safe


def static_page_view(request, slug, language):
    page = get_object_or_404(StaticPage, slug=slug, language=language)
    try:
        html = page.html_file.read().decode('utf-8')
    except Exception:
        raise Http404("Unable to read static page content.")
    return HttpResponse(mark_safe(html))


def dynamic_page_view(request, slug, lang):
    language = get_object_or_404(Language, slug=lang)
    page = get_object_or_404(DynamicPage, slug=slug, language=language)
    try:
        html = page.render_to_string()
        return HttpResponse(mark_safe(html))
    except Http404:
        raise
    except Exception as e:
        print(e)
        raise Http404("Unexpected error during rendering.")


def image_view(request, slug):
    image = get_object_or_404(Image, slug=slug)
    return redirect(image.image.url)
