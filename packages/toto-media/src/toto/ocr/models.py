"""OCR has no database models.

The app is a stateless Media sub-tab: upload a screenshot, run Tesseract (see
``toto.ocr.ocr.OcrHelper``), show the text, optionally save the screenshot into a
vault bucket, and forward the text to the ingestor. The old
workspace/image/transform models were dropped in the migration now squashed into
``0001_squashed_0002`` — read its docstring before adding a model here, as the
app currently declares no migration dependencies at all on purpose.
"""
