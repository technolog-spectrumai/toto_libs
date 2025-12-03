from django.shortcuts import render, get_object_or_404
from .models import Page
from oya.page import PageProcessor


def page_detail(request, slug):
    """
    Display a single Page by slug, decorated with PageProcessor.
    """
    page = get_object_or_404(Page, slug=slug)

    # Base context
    context = {
        "page": page,
        "title": page.title,
        "body": page.body,
    }

    # 🎨 Decorate with PageProcessor (adds theme, colors, etc.)
    decorated_context = PageProcessor().decorate(context, request)

    return render(request, "webfront/page.html", decorated_context)
