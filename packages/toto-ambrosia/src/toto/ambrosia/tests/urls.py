"""The host's routes plus the test lab's. For ambrosia's own tests only.

Applied through `override_settings(ROOT_URLCONF=testlab.URLCONF)` by
`base.TestlabTestCase`. It extends the host's urlconf rather than replacing
it: the lobby and the room extend base templates that reverse the host's own
names (vault:, core:, sso:), and a urlconf holding only the test lab would
answer every page with a NoReverseMatch.
"""

from importlib import import_module

from django.core.exceptions import ImproperlyConfigured
from django.urls import include, path

from toto.ambrosia.urls import workspace_urlpatterns

from . import testlab

if testlab.HOST_URLCONF == testlab.URLCONF:
    raise ImproperlyConfigured(
        "toto.ambrosia.tests.testlab was first imported while its own urlconf "
        "was already ROOT_URLCONF, so it cannot tell which urlconf is the host's.")

urlpatterns = [
    # First, so no host pattern can shadow them.
    path("ambrosia-testlab/",
         include((workspace_urlpatterns(), testlab.NAMESPACE))),
    # The workspace API. A host mounts it only beside Compute Capsules, and
    # zenobia stopped doing so on 2026-09-14. It is mounted here so that what
    # it answers without them is asserted rather than assumed.
    path("api/v1/", include("toto.ambrosia.api_urls")),
    *import_module(testlab.HOST_URLCONF).urlpatterns,
]
