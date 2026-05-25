# toto.ocr

*(Studio only — requires BUILD_STUDIO=1)*

Document OCR pipeline. Images uploaded to a vault bucket are processed through a configurable transform chain to extract text line by line.

## Purpose

An operator creates an `OcrProject` backed by a vault `Bucket`, then uploads document images. Each `OcrImage` is queued for processing through an ordered chain of `ImageTransform` steps (resize, grayscale, threshold…) before the OCR engine extracts `OcrLine` records with bounding boxes and confidence scores. Custom transforms delegate to `workflows.LambdaFunction`. Extracted text feeds downstream into palimpsest pages or memo decks.

## Models

- `OcrProject` — a named OCR workspace. Fields: `name`, `slug`, `bucket` (FK to `vault.Bucket`), `owner` (FK to `auth.User`), `allowed_users` (M2M to `auth.User`), `language`, `is_active`, `created_at`.

- `OcrImage` — one image in a project queued for OCR. Fields: `project` FK, `vault_file` (FK to `vault.VaultFile`), `status` (`pending / processing / done / failed`), `page_number`, `processed_at`, `error_message`.

- `OcrLine` — a single text line extracted from an image. Fields: `image` FK, `line_number`, `text`, `confidence` (float 0–1), `bounding_box` (JSON — `{x, y, w, h}` as fractions of image size).

- `ImageTransform` — a processing step applied to images before OCR. Fields: `project` FK, `name`, `order`, `transform_type` (`resize / grayscale / threshold / denoise / deskew / crop`), `lambda_function` (FK to `workflows.LambdaFunction`, nullable — custom transform), `is_active`.

- `ImageTransformParam` — a named parameter for a transform. Fields: `transform` FK, `key`, `value` (string).

## Key coupling

- `vault.VaultFile` / `vault.Bucket` — images are stored in vault.
- `workflows.LambdaFunction` — custom transform steps delegate to workflow lambdas.

## Dependencies

- `vault` — OcrImage.vault_file FK to VaultFile; project backed by vault.Bucket
- `workflows` — ImageTransform.lambda_function FK to workflows.LambdaFunction
