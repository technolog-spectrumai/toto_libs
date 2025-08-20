from django.shortcuts import render, get_object_or_404
from django.template import Template as DjangoTemplate, Context
from .models import Page, Image
from django.shortcuts import get_object_or_404, redirect


def page_list(request, language):
    pages = Page.objects.select_related('template', 'author').filter(language=language)
    return render(request, 'netogami/page_list.html', {
        'pages': pages,
        'language': language
    })

def render_template(template_obj, context_data):
    context = Context(context_data or {})
    rendered_html = DjangoTemplate(template_obj.content or '').render(context)
    return rendered_html
\

def page_detail(request, language, slug):
    page = get_object_or_404(Page, language=language, slug=slug)

    try:
        rendered_html = render_template(page.template, page.data)
    except Exception as e:
        rendered_html = f"<pre style='color:red;'>Template rendering error: {e}</pre>"

    return render(request, 'netogami/page_detail.html', {
        'page': page,
        'rendered_html': rendered_html,
    })

def image_view(request, slug):
    image = get_object_or_404(Image, slug=slug)
    return redirect(image.image.url)

