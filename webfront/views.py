from .models import WebPage, Language
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.safestring import mark_safe


def static_page_view(request, slug, lang):
    language = get_object_or_404(Language, slug=lang)
    page = get_object_or_404(WebPage, slug=slug, language=language)
    html = page.get_html_content()
    if not html:
        raise Http404("Unable to read static page content.")
    return HttpResponse(mark_safe(html))
