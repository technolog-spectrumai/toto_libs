"""The reading half of toto.memo: a gallery, a viewer, a player, an export.

WHY A SECOND URLCONF. A host can want decks to be *shown* without wanting them
authored here — zenobia does: presentations are made in the zinnia desktop app
and arrive as vault files, and the browser editor brings TipTap, cyprian's
static bundle and a paid entitlement with it. Mounting `memo.urls` would
register `new`, `save`, `delete` and the two media routes as well, and there is
no setting that removes a route from a urlconf.

WHY NOT A FLAG INSIDE `urls.py`. A flag read at import time is one more thing
that can disagree with what is installed. Two urlconfs cannot disagree: a host
picks the one it means, and `reverse("memo:save")` either resolves or raises.
`views.py` treats NoReverseMatch as "do not offer the link" — see
`_maybe_reverse` — so the reading pages degrade rather than break.

WHAT IS DELIBERATELY ABSENT, and what each absence costs:

    new, save, delete       no authoring at all; decks arrive as vault files
    media_embed, upload     the editor's asset routes, useless without it

`export_pdf` STAYS. It is a GET, it is free by design (a lapsed plan can still
get its own decks out), and it is the reading half's most useful verb.

`app_name` is "memo" here too, deliberately: the namespace is the app's
identity to `SubscriptionGateMiddleware` and to every template link, and a
second name would silently unhook both.
"""

from django.urls import path
from django.views.generic import RedirectView

from .views import (
    PresentationIndexView,
    PresentationReadView,
    PresentationView,
    presentation_export_pdf,
)

app_name = "memo"

#: `RedirectView` answers every verb by default, so a POST to `/memo/` would
#: 302 rather than 405 — and on this urlconf a POST has nowhere legitimate to
#: go at all.
SAFE_ONLY = ["get", "head", "options"]

urlpatterns = [
    path('', RedirectView.as_view(http_method_names=SAFE_ONLY,
                                  pattern_name='memo:gallery',
                                  query_string=True),
         name='index'),
    path('gallery/', PresentationIndexView.as_view(), name='gallery'),
    path('read/<int:file_pk>/', PresentationReadView.as_view(), name='read'),
    path('present/<int:file_pk>/', PresentationView.as_view(), name='present'),
    path('export/<int:file_pk>/pdf/', presentation_export_pdf, name='export_pdf'),
]
