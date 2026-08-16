"""Showing a deck, and nothing else.

Read-only since 8/2026: decks are authored in the zinnia desktop app and arrive
here as vault files. What the server still owes them is a way to be READ — the
index, a page, the full-screen player, and the PDF export — so a deck somebody
made is not a file you can only download.

There is no edit, no save and no media upload. Those left with the editor, and
their absence is the point: nothing here can change a deck.
"""
from django.urls import path

from .views import (
    PresentationIndexView,
    PresentationReadView,
    PresentationView,
    presentation_export_pdf,
)

app_name = 'memo'

urlpatterns = [
    path('', PresentationIndexView.as_view(), name='index'),
    path('read/<int:file_pk>/', PresentationReadView.as_view(), name='read'),
    path('present/<int:file_pk>/', PresentationView.as_view(), name='present'),
    path('export/<int:file_pk>/pdf/', presentation_export_pdf, name='export_pdf'),
]
