# toto.transcription — parked 2026-07-29 (toto v1.21)

Whisper speech-to-text over vault audio and video: an eight-model persistence layer
(collections, sources, jobs, segments, speakers, artifacts, events, model weights)
plus ~900 lines of service code that ran openai-whisper or faster-whisper, exported
`.txt`/`.srt`/`.vtt`/`.json`, and downloaded model weights from HuggingFace.

## Why it was parked

It had no user interface at all, and most of it was unreachable.

- **No `views.py`, no `urls.py`, no templates.** It was absent from every host's url
  tree because it had nothing to mount. The only ways in were the django admin and
  another app's page.
- **Four dead `reverse()` calls** — `models.py:89`, `:194`, `:200`, `:448` pointed at
  `transcription:collection_detail`, `:source_detail`, `:source_manage` and
  `:model_setup`. None of those names existed anywhere; all four were wrapped in a
  bare `try/except` returning `""`. They are the fossils of a UI that was removed
  long before this, and they are the spec for one, should anybody want it back.
- **Nothing outside the app touched any of its models.** The only reachable code
  path was `transcribe_demo_file()` (`services.py:604`), which builds *unsaved*
  in-memory job and source objects and writes zero rows. So the eight tables, the
  segment/speaker/artifact/event machinery and the whole `SpeechModel` download flow
  were orphaned in every deployment.
- **Its dependencies were never installed.** `openai-whisper` / `faster-whisper`
  (and transitively torch or ctranslate2) appear in no host's requirements; the
  backends raise `RuntimeError` when they are missing. So on every real deployment
  the feature could not have run even if it had been reachable.
- It had **no build flag of its own** — it rode inside `FEATURE_APPS["media"]`, which
  is what the 1.21 flag split set out to fix. Rather than invent a flag and a UI for
  an app nothing used, it comes here.

Its surface elsewhere was removed in the same release: the `transcribe` tab in
`toto.manta`'s command builder, `manta.views.quick_transcribe`, the
`_quick_transcribe.html` partial, `manta/commands/transcribe.py` and the
`WhisperCommand` service backend.

## What it leaves behind

Nothing in code. In an existing database the eight `transcription_*` tables remain,
untouched — the same treatment `toto.sketch` got. They are orphaned, not dropped, so
any real transcripts survive a revival. Dropping them is a deliberate, separate act.

`APPS_TO_SYNC` on zenobia lost its `"transcription"` label in the same commit: a
label there for an uninstalled app makes `backup_engine.iter_models()` raise
`LookupError`, which breaks *every* backup and restore path, not just this one.

## Reviving it

The app itself is self-contained and migrates cleanly: its foreign keys point only
at `vault.VaultFile`, `vault.Bucket`, `people.Person` and the user model, all of
which are `CORE_APPS`. To bring it back you would need, in order:

1. `pip` deps — `openai-whisper` or `faster-whisper`, plus `ffmpeg` on PATH for the
   openai-whisper path.
2. A home in a package and a `BUILD_TRANSCRIPTION` flag. Note it hard-imports
   `toto.fileservices` at module scope in `plugins/file_service_plugins.py`, and its
   plugin is only ever registered by `fileservices.apps.ready()` — so
   `transcription → fileservices` is a real closure, not a preference.
3. A UI. The four dead reverses above say what it should contain.
4. `"transcription"` back in `TASK_MODULES` — it has two celery tasks, one with a
   7200-second time limit, which is why the broker's `visibility_timeout` was raised
   to 7800s. That setting can stay either way.
