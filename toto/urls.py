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
    path("memo/", include("memo.urls", namespace="memo")),
    path("webfront/", include("webfront.urls")),
    path("kanban/", include("kanban.urls", namespace="kanban")),
    path("forum/", include("forum.urls", namespace="forum")),
    path("portfolio/", include("portfolio.urls", namespace="portfolio")),
    path("vault/", include("vault.urls", namespace="vault")),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)