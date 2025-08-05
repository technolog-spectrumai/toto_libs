from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect


admin.site.site_header = 'Nasza Aplikacja'
admin.site.index_title = 'Nasze Sprawy'
admin.site.site_title = 'Administracja'

urlpatterns = [
    path('admin/', admin.site.urls),
    path("nest/", include("oya.urls")),
    path('captcha/', include('captcha.urls')),
    path('', lambda request: redirect('nest/root')),
    path('netogami/', include('netogami.urls')),
    path("gervazy/", include("gervazy.urls"))
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
