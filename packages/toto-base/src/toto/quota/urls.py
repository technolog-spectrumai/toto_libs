from django.urls import path

from . import views

app_name = "quota"

urlpatterns = [
    path("", views.index, name="index"),
    path("me/", views.my_usage, name="my_usage"),
    path("me/<str:app_label>/", views.my_usage, name="my_usage_app"),
    path("taxes/", views.taxes, name="taxes"),
    # The dials. Split from quota:index so the catalogue is a place you
    # can stay rather than a link to the editor.
    path("desk/", views.fees_desk, name="fees_desk"),
    # Path and url-name both unchanged, though the page is now operator-only and
    # is titled "Income": a live host has these bookmarked, and the restructure
    # was never worth breaking a link over.
    path("fees/", views.fees, name="fees"),
    # Every concrete path belongs ABOVE this one. Metric codes are dotted
    # (texlab.compile), which <str:> matches happily — it excludes "/" only, so
    # a new segment declared after this line is swallowed as a metric code and
    # 404s as "no metric registered as 'taxes'".
    path("<str:code>/", views.metric_detail, name="metric_detail"),
]
