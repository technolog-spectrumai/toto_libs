"""Static files storage that survives missing third-party sub-assets.

Vendored, minified third-party CSS/JS (reveal.js, chart.js, leaflet, …) routinely
reference files we do not fully vendor — webfont CSS, source maps, images — via
relative ``url(...)`` / ``@import``. WhiteNoise's stock
``CompressedManifestStaticFilesStorage`` **hard-fails** ``collectstatic`` on the
first such unresolved reference (``MissingFileError``), and a missing manifest
entry then 500s the page at request time.

That turns one missing third-party sub-asset into a dead deployment. This backend
keeps the same hashing + compression but degrades gracefully instead:

* during ``collectstatic`` an unresolvable reference is left **as-is** (served at
  its original, unhashed path, or 404ing on its own) rather than aborting the build;
* at request time an unknown lookup falls back to the unhashed name
  (``manifest_strict = False``) instead of raising.

So a missing font/source-map costs you that one asset, never the whole site.
"""

from __future__ import annotations

from whitenoise.storage import CompressedManifestStaticFilesStorage


class ResilientManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    manifest_strict = False

    def hashed_name(self, name, content=None, filename=None):
        try:
            return super().hashed_name(name, content=content, filename=filename)
        except ValueError:
            # A referenced file (font / source map / image) was not collected.
            # WhiteNoise raises MissingFileError (a ValueError subclass) here; keep
            # the reference pointing at the raw path instead of failing the build.
            return name
