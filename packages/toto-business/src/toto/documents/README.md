# toto.documents — the Business Center's exports

Builds the HTML for an export and hands it to `toto.aralia`. **Owns no
renderer**, and a test asserts it imports none.

That split is why there is no second PDF engine to keep in step, and why
aralia's SSRF refusal, its metering and its "never inline" dispatch apply to
Business Center exports for free.

## Why these build strings, not Django templates

A `render_to_string` would put the page's base template, its Alpine attributes
and the site stylesheet into a document going to a printer — and then somebody
spends an afternoon finding out why the PDF has a navigation bar in it. What
goes to WeasyPrint is written in `builders.py`, in full, with its own print CSS.
`tests/test_export.py` asserts no page furniture leaks in.

## The ledger export

The chain in reading order, its verification verdict, and a **QR that verifies
it** — embedded as a `data:` URI, because aralia fetches nothing. The payload
text is printed beside the picture: a code that will not scan still has to be
usable.

The checkpoint is taken **at export time**, not reused. A printed ledger whose
code attests to a state from three months ago would verify as `MATCH_GROWN` and
tell the reader nothing about the document in their hand.

## What is not here yet

The voting and meeting exports. They land in Stage 4 with the app that produces
them; `builders.py` is where they go.
