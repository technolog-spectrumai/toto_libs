from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from django.views.generic import RedirectView
from django.urls import path, include, reverse_lazy


admin.site.site_header = 'Nasza Aplikacja'
admin.site.index_title = 'Nasze Sprawy'
admin.site.site_title = 'Administracja'

urlpatterns = [
    path('admin/', admin.site.urls),
    path("nest/", include("oya.urls", namespace='nest')),
    path("community/", include("community.urls", namespace='community')),
    path('captcha/', include('captcha.urls')),
    #path(r'^_nested_admin/', include('nested_admin.urls')),
    path(
        '',
        RedirectView.as_view(
            url=reverse_lazy('nest:root'),
            permanent=not settings.DEBUG
        )
    ),
    path('', lambda request: redirect('nest/root')),
    path("gervazy/", include("gervazy.urls")),
    path("memo/", include("memo.urls")),
    path("resume/", include("resume.urls")),
    path("webfront/", include("webfront.urls")),
    path("documents/", include("documents.urls")),
    path("kanban/", include("kanban.urls", namespace="kanban")),
    # path('.well-known/acme-challenge/<path:path>', serve, {
    #     'document_root': settings.ACME_CHALLENGE_ROOT,
    # }),
    #path('accounts/', include('django.contrib.auth.urls'))
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
