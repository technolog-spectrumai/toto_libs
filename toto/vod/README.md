# toto.vod

Reusable Django VOD app for industrial robotics footage.

This version is intentionally **connected** to your existing Toto apps:

- **Storage** is reused from `vault`:
  - source videos are `vault.VaultFile`
  - collections point to `vault.Bucket`
  - generated HLS output uses the same Django storage backend and stores paths on `VodVideo`
- **Subscriptions** are reused from `toto.subscriptions`:
  - subscriber-gated videos point to `subscriptions.SubscriptionPlan`
  - access checks use active/trialing `subscriptions.Subscription`
  - watch-time metering calls `toto.subscriptions.services.record_usage`
  - local playback events can link to `subscriptions.SubscriptionUsage`
- **Invoices** are reused from the existing `invoices` app:
  - invoice-gated videos create an `invoices.Invoice`
  - `VodAccessGrant` unlocks only after the linked invoice is paid

The app does **not** reimplement subscription plans, customers, invoices, payments, ledgers, assets, or vault storage.

## Install

```python
INSTALLED_APPS = [
    # existing dependencies first
    "toto.vault",          # app_label: vault
    "toto.subscriptions",  # app_label: subscriptions
    "toto.invoices",       # app_label: invoices
    "toto.vod",
]
```

Project URLs:

```python
path("vod/", include("toto.vod.urls")),
```

Then:

```bash
python manage.py migrate
```

## Optional settings

```python
VOD_FFMPEG_BIN = "ffmpeg"
VOD_FFMPEG_VIDEO_CODEC = "libx264"
VOD_FFMPEG_AUDIO_CODEC = "aac"
VOD_FFMPEG_AUDIO_BITRATE = "128k"
VOD_FFMPEG_PRESET = "veryfast"

# Optional when your User -> people.Person relation is custom.
VOD_PERSON_RESOLVER = "path.to.resolve_person"  # callable(user) -> people.Person

# Optional custom active subscription resolver.
VOD_SUBSCRIPTION_RESOLVER = "path.to.resolve_subscription"  # callable(user, plan) -> Subscription | None
```

## Access modes

`VodCollection.access_mode` controls the default:

- `public`
- `unlisted`
- `subscribers`
- `invoice`
- `staff`

`VodVideo.access_mode` defaults to `inherit`, but can override the collection.

For subscriber-gated videos, set `required_plan` to an existing `subscriptions.SubscriptionPlan`.
For invoice-gated videos, set `invoice_amount` / `invoice_currency_label`; paying is handled by your existing invoices app.

## Upload and HLS

Upload in the UI:

```text
/vod/upload/
```

Import a server-side robotics video:

```bash
python manage.py ingress_robotics_vod /path/to/robot-cell.mp4 --publish --build-hls
```

Build HLS output:

```bash
python manage.py build_vod_hls 123 --force
```

This produces storage paths like:

```text
vod/hls/industrial-robotics/robot-cell/index.m3u8
vod/hls/industrial-robotics/robot-cell/segment_00000.ts
vod/hls/industrial-robotics/robot-cell/segment_00001.ts
```

The browser page uses HLS.js when native HLS support is unavailable.

## Notes

- VOD source files must be unencrypted `vault.VaultFile` records with `file_type="video"`.
- HLS/TS files are public/media-storage assets. Put S3/CloudFront in front of the storage backend for production.
- The app expects app labels `vault`, `subscriptions`, and `invoices`.
