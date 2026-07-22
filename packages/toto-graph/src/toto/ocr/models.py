"""OCR has no database models.

The app is a stateless Knowledge-Graph sub-tab: upload a screenshot, run
Tesseract (see ``toto.ocr.ocr.OcrHelper``), show the text, optionally save the
screenshot into a vault bucket, and forward the text to the ingestor. The old
workspace/image/transform models were dropped in migration 0002.
"""
