# toto.manta — parked 2026-07-29 (toto v1.21)

The media command builder: pick a vault file, assemble an `ffmpeg` or `ffprobe`
command through a form, run it on a celery worker, get the outputs back as new
vault files. A `FileJob` model (plus a `MediaJob` proxy), a command-class registry
with 16 operations across two tabs, a form/builder/factory layer that turned tab
fields into an argv, and `run_direct_job` — a task with a 7200-second soft limit.

Parked together with [fileservices](../fileservices/PARKED.md); the two are the
ffmpeg half of the platform and were retired as one act.

## Why it was parked

Not because it was broken — unlike `transcription`, this app worked and had a real
UI. It was retired because the section it anchored was being dismantled around it
and what remained did not justify a native binary in every image.

- **Its collaborators were gone.** `transcription` (parked earlier in the same
  release, for cause) and `fileservices` were the rest of the media section. With
  fileservices gone, manta loses the vault wand's entry point — the way most people
  actually reached it — leaving only its own `/manta/` page.
- **ffmpeg existed for this app and one other.** The apt layer, `INSTALL_FFMPEG`
  and `Features.ffmpeg` had exactly two consumers. Retiring both takes a native
  binary out of every image; nothing else in the tree shells out to ffmpeg.
- **It was the last thing forcing celery on an otherwise-lean host.** manta needs
  the worker (`views.py:305` calls `run_direct_job.delay()`, and
  `tasks_direct.py:4` imports `celery` at module scope), so `BUILD_MANTA=1` meant
  the realtime pip layer whether or not anything else needed it.

What survives of the media section is playback and OCR: `toto.vod` (no models, no
tasks, no binary) and `toto.ocr` (tesseract, synchronous). Both are cheap.

## What this app was NOT parked for

Worth recording, because the 1.21 flag-split investigation established the
opposite of what the code's comments implied, and a future revival should not
re-inherit the wrong idea:

- **manta did not need `fileservices`.** Its two references to it were both safe
  with fileservices absent: `access.py:28` imports `user_can_access_vault_file`,
  a pure function with no Django imports, and `plugins/file_service_plugins.py` is
  only ever imported by *fileservices'* own `autodiscover_plugins`, i.e. the
  inbound direction. (The two genuinely unguarded imports — in
  `commands/backends.py` and `views.quick_transcribe` — went away with the
  transcribe surface earlier in this release.)
- **manta did not need `workflows`.** It has exactly one FK, `FileJob.owner → User`.
  Input and output VaultFiles are raw id lists in JSONFields. It names workflows
  nowhere. It reached workflows only *through* fileservices.

So a revival needs celery and ffmpeg, and nothing else.

## What it leaves behind

**In code:** nothing. Nothing outside the app imported it — the plugin
registration was pull-only, and `TASK_MODULES` lost its `"toto.manta"` entry in
the same commit.

**In the database:** `manta_filejob` remains, untouched — orphaned, not dropped,
as with `sketch`, `transcription` and `fileservices`. `MediaJob` is a proxy model
and has no table of its own.

`APPS_TO_SYNC` on zenobia lost its `"manta"` label in the same commit, for the
`backup_engine.iter_models()` → `LookupError` reason spelled out in the sibling
files.

## Removed from this app earlier in the same release

The whole transcribe surface, when `transcription` was parked: the `transcribe`
tab (`TAB_ORDER` is now `("ffmpeg", "ffprobe")`), `commands/transcribe.py`,
`WhisperCommand` and `ServiceCommand` from `commands/backends.py`,
`views.quick_transcribe` and its url, `_save_text_file`, and
`templates/manta/_quick_transcribe.html`. `OPERATIONS` went from 17 to 16. Its
tests went with it. So what is parked here is the ffmpeg/ffprobe builder alone,
already coherent — not a half-dismantled app.

## Reviving it

1. A home in a package and a `BUILD_MANTA` flag. Add it to the `realtime`
   derivation in `toto.features` (**not** the workflows closure) — what it needs
   is celery, which is what that tier's pip layer installs. Without it,
   `from celery import shared_task` fails at boot.
2. `INSTALL_FFMPEG` + the apt layer back in the Dockerfile, and `services.celery`
   in the deploy profile — the flag buys the pip layer, not a worker container, and
   jobs queue forever without one.
3. `"toto.manta"` back in `registry.TASK_MODULES` and `"manta"` in `APPS_TO_SYNC`.
4. Optionally revive `fileservices` too, for the vault wand entry point. Not
   required: `/manta/` stands alone.
