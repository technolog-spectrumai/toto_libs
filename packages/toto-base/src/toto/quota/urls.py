from django.urls import path

from . import views

app_name = "quota"

urlpatterns = [
    path("", views.index, name="index"),
    path("me/", views.my_usage, name="my_usage"),
    path("me/<str:app_label>/", views.my_usage, name="my_usage_app"),
    path("rates/", views.rate_desk, name="rate_desk"),
    # Every concrete path belongs ABOVE this one. Metric codes are dotted
    # (texlab.compile), which <str:> matches happily — it excludes "/" only, so
    # a new segment declared after this line is swallowed as a metric code and
    # 404s as "no metric registered as 'rates'".
    path("<str:code>/", views.metric_detail, name="metric_detail"),
    path("<str:code>/overrides/<int:pk>/delete/", views.override_delete, name="override_delete"),
]
