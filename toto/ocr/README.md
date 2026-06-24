# toto.ocr

*(Knowledge-Graph build — requires BUILD_NEO4J=1; needs the Tesseract binary + pytesseract)*

A small, stateless **OCR sub-tab inside the Knowledge Graph** (ravioli tab bar).
There are **no database models** — the flow is entirely request/response.

## Flow

1. **Load screenshot** — the user picks an image in the OCR tab.
2. **Run OCR** — `ocr:run` feeds the upload to `OcrHelper` (pytesseract) and returns the
   extracted text as JSON.
3. **Save to bucket (optional)** — a checkbox unfolds a bucket dropdown; when set, the
   screenshot is stored as an `image` `vault.VaultFile` in the chosen bucket.
4. **Ingest text** — the extracted (and editable) text is POSTed to `ingestor:generate`,
   then the user is taken to the resulting ingestor proposal.

## Pieces

- `ocr.py` — `OcrHelper`: the pytesseract wrapper (run + line grouping). The only engine.
- `views.py` — `ocr_home` (renders the tab) and `ocr_run` (Tesseract + optional vault save).
- `templates/ocr/ocr.html` — Alpine `ocrFlow()` UI; includes `ravioli/_tabs.html`.

## Dependencies

- `vault` — optional save creates a `VaultFile` in a user-owned `Bucket`.
- `ingestor` — the "Ingest text" button reuses `ingestor:generate`.
- System: the `tesseract-ocr` binary + language packs, and the `pytesseract` package.
