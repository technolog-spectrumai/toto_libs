from django.shortcuts import render, get_object_or_404
from django.template import Template as DjangoTemplate, Context
from .models import Page

def page_list(request, language):
    pages = Page.objects.select_related('template', 'author').filter(language=language)
    return render(request, 'netogami/page_list.html', {
        'pages': pages,
        'language': language
    })

def render_parts(template_obj, context_data):
    context = Context(context_data or {})
    rendered_head = DjangoTemplate(template_obj.header or '').render(context)
    rendered_body = DjangoTemplate(template_obj.content or '').render(context)
    return rendered_head, rendered_body

def page_detail(request, language, slug):
    page = get_object_or_404(Page, language=language, slug=slug)

    try:
        rendered_head, rendered_body = render_parts(page.template, page.data)
    except Exception as e:
        rendered_head = ''
        rendered_body = f"<pre style='color:red;'>Template rendering error: {e}</pre>"

    return render(request, 'netogami/page_detail.html', {
        'page': page,
        'rendered_head': rendered_head,
        'rendered_body': rendered_body,
    })
