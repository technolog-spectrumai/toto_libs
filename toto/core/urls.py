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
    path('', lambda request: redirect('nest:home')),
    path("vault/", include("vault.urls", namespace="vault")),
    path("audit/", include("audit.urls", namespace="audit")),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)