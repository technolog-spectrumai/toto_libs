# toto.documents — the Business Center's exports

Builds the HTML for an export and hands it to `toto.aralia`. **Owns no
renderer**, and a test asserts it imports none.

That split is why there is no second PDF engine to keep in step, and why
aralia's SSRF refusal, its metering and its "never inline" dispatch apply to
Business Center exports for free.

## Why these build strings, not Django templates

A `render_to_string` would put the page's base template, its Alpine attributes
and the site stylesheet into a document going to a printer — and then somebody
spends an afternoon finding out why the PDF has a navigation bar in it. The
body is written in `builders.py`, in full. `tests/test_export.py` asserts no
page furniture leaks in.

## How an export reaches aralia (2026-10-01)

Aralia renders an **approved source** and nothing else — a vault page the
requester may read, or a wiki page — and its `create_run` takes the resolved
source; raw HTML is the door it closed. So `services.export` does not hand
aralia HTML. It runs aralia's `generate` steps, in that order:

1. quota and funds (`aralia.billing.check_before_dispatch`) and the
   letterhead (`aralia.letterhead`, which refuses with no logo) — a refusal
   here files nothing;
2. the built document is filed as an HTML page in the requester's personal
   bucket, folder **"Business Center exports"** (`file_page`), named after
   the export's label (`-2` when the name is taken);
3. aralia resolves that page as a `file:` source — the platform's document
   sanitiser, the body without its head — and the run is made from it, on
   the `federal` letterhead with the provenance footer;
4. the run is queued on the `pdf` worker; a refusal closes it, and the page
   stays to be rendered later from `/aralia/`.

The PDF lands beside its page. Nothing is charged until it exists
(`aralia.billing.settle`), so a refused export costs nothing and has nothing
to refund. The page's own `<style>` does not print — the letterhead's does —
which is why the QR is drawn at its print size (`builders.QR_SCALE`) rather
than sized by CSS.

## The ledger export

The chain in reading order, its verification verdict, and a **QR that verifies
it** — embedded as a `data:` URI, because aralia fetches nothing. The payload
text is printed beside the picture: a code that will not scan still has to be
usable.

The checkpoint is taken **at export time**, not reused. A printed ledger whose
code attests to a state from three months ago would verify as `MATCH_GROWN` and
tell the reader nothing about the document in their hand.

## The other exports

A meeting record and a single decision (`vote_document`, `meeting_document`,
from `toto.company`'s voting views) and the shareholder register
(`register_document`). All go through `services.export`.
