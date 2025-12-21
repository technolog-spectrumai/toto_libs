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
    path('', lambda request: redirect('nest:home')),
    path("memo/", include("memo.urls", namespace="memo")),
    path("vault/", include("vault.urls", namespace="vault")),
    path("community/", include("community.urls", namespace="community")),
    path("events/", include("events.urls", namespace="events")),
    path("audit/", include("audit.urls", namespace="audit")),
    path("kanban/", include("kanban.urls", namespace="kanban")),
    path("finance/", include("finance.urls", namespace="finance")),
    path("federal/", include("federal.urls", namespace="federal")),
    path("gate/", include("gate.urls", namespace="gate")),
    path("ravioli/", include("ravioli.urls", namespace="ravioli")),
    path("webfront/", include("webfront.urls", namespace="webfront")),
    path("assets/", include("assets.urls", namespace="assets")),
    path("portfolio/", include("portfolio.urls", namespace="portfolio")),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)