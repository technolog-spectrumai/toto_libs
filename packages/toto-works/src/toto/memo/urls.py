"""Showing a deck, and — since 8/2026 — authoring one again.

Read-only from 8/2026 until the editors came back: decks were authored in the
zinnia desktop app and arrived here as vault files. The browser editor is back,
under the shared regime in `toto.vault.editing` — one lock, one base-hash
precondition, one version per save, one meter.

The split by METHOD is load-bearing, not stylistic. `SubscriptionGateMiddleware`
reads `resolver_match.app_name`, lets safe methods through and answers 402 to
everything else, so this table is where the tier is expressed:

    index / read / present / edit      GET   — free to anyone who may see it
    export_pdf                         GET   — free; a lapsed plan can still
                                               get its own decks out
    new / save / delete / media_*      POST  — Professional

`edit` is a GET because the PAGE is a read; what it may DO is decided inside it
by `editing.door_for`, which renders the plans page for a subscriber without the
entitlement. Making it a POST would paywall looking; making `save` a GET would
unpaywall writing. `zenobia.tests.test_editors_are_gated` asserts exactly this.
"""
from django.urls import path
from django.views.generic import RedirectView

from . import views
from .views import (
    PresentationEditView,
    PresentationIndexView,
    PresentationReadView,
    PresentationView,
    presentation_export_pdf,
)

app_name = 'memo'

#: A redirect still has to refuse what the app refuses. `RedirectView` answers
#: every verb by default, so a POST to this prefix would 302 instead of 405 —
#: and for toto.htmlview that would break the promise its own urls.py makes and
#: `primula/tests/test_readonly.py` walks this table to enforce.
SAFE_ONLY = ["get", "head", "options"]

urlpatterns = [
    # `/memo/` is Office's Presentations tab now — one place to look for the
    # things you make, rather than one flat list per app. The NAME stays put so
    # every existing reverse() and template link keeps working.
    #
    # The gallery is the index again. /memo/ redirected to Office's
    # Presentations tab from 2026-08 until Office retired to limbo on
    # 2026-09-02; the gallery had been kept the whole time because it renders
    # a real cover slide per deck and serves ANONYMOUS visitors, both of which
    # the hub never did. NOTE the old target was an unguarded
    # `office:section` reverse in an unconditionally-installed app — on any
    # build without Office mounted, GET /memo/ was a NoReverseMatch 500.
    path('', RedirectView.as_view(http_method_names=SAFE_ONLY,
                                  pattern_name='memo:gallery',
                                  query_string=True),
         name='index'),
    path('gallery/', PresentationIndexView.as_view(), name='gallery'),
    path('read/<int:file_pk>/', PresentationReadView.as_view(), name='read'),
    path('present/<int:file_pk>/', PresentationView.as_view(), name='present'),
    path('export/<int:file_pk>/pdf/', presentation_export_pdf, name='export_pdf'),

    path('new/', views.presentation_create, name='create'),
    path('edit/<int:file_pk>/', PresentationEditView.as_view(), name='edit'),
    path('edit/<int:file_pk>/save/', views.presentation_save, name='save'),
    path('edit/<int:file_pk>/delete/', views.presentation_delete, name='delete'),
    path('media/embed/', views.presentation_media_embed, name='media_embed'),
    path('media/upload/', views.presentation_media_upload, name='media_upload'),
]
