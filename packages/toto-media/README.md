# toto-media

`toto-media` is the video, audio and transcription tier of the toto suite: **"toto video, transcription and media processing."** It bundles four Django apps under the shared `toto.*` namespace — **fileservices**, **manta**, **transcription** and **vod** — that together let operators run ffmpeg/ffprobe pipelines, transcribe speech to text with local Whisper models, and play media back in the browser. All work reads from and writes to the shared vault storage, and long jobs run through the toto workflow/Celery engine with an inline fallback. Because these apps are heavy (ffmpeg, Whisper weights), the wheel is only installed on media-capable hosts.

---

## What it does (functional)

From an operator's point of view, `toto-media` adds media processing on top of files already stored in the vault:

- **Run media tools on a file (Manta command builder).** Pick a video or audio file and build, preview and run a media command: compress, resize, crop, change FPS, cut/trim, extract MP3, remove or replace audio, add subtitles or a watermark, make a thumbnail or a GIF, stack videos (vstack/hstack), concatenate clips, or inspect a file with ffprobe. You see the exact command before it runs, and outputs are saved back into the vault next to the source file.
- **Transcribe speech to text.** Turn an audio file into a text transcript plus `.srt` subtitles using a local Whisper engine — no cloud service required. A "quick transcribe" path and a dedicated transcribe tab make this a one-click operation from the vault.
- **Manage transcription libraries.** Group sources into collections, keep timestamped transcript segments with optional speaker labels, track per-source analytics (impressions, plays, exports, downloads), and export transcripts as **TXT, SRT, VTT or JSON**. Collections are access-controlled (public or private with reader/writer lists).
- **Bring your own speech model.** Register Whisper models in the admin, upload weights or have them downloaded automatically (with progress), and choose CPU or GPU inference — all without editing settings files.
- **Play media in the browser.** Video and audio files in the vault get a "Play" action that opens an in-page player, with access gated the same way the vault gates the file.
- **Automate it.** Any file service can be enqueued as a background job and tracked as a workflow run, so media steps can be chained into larger toto workflows.

Everything is layered on the vault: sources, generated transcripts and processed media are all ordinary vault files, owned and permissioned like any other file.

---

## How it works (technical)

`toto-media` ships four cooperating Django apps plus a shared plugin/dispatch layer. The unifying idea is the **file service**: a background operation over a `vault.VaultFile`. `fileservices` owns the run record and the dispatch/execution machinery; `manta` and `transcription` register services and provide the builder UIs; `vod` is a thin playback host.

Cross-app couplings worth knowing up front:

- **Storage is always the vault.** Inputs are `vault.VaultFile`; outputs are new `VaultFile`s saved into the same bucket/directory as their source.
- **Dispatch goes through workflows/Celery.** `fileservices.dispatch.dispatch_run()` wraps each run in a one-node `WorkflowRun` (workflow slug `fileservices-run`) when Celery is available, falling back to a plain Celery task, and finally to **inline synchronous execution** when no broker is up (`toto.celery_utils.celery_available()`).
- **Manta transcribe reuses the fileservices whisper backend.** The manta transcribe command creates a `FileServiceRun` (service key `transcription`), whose plugin calls into `toto.transcription.services`. A `FileServiceRun` reaching a terminal state is mirrored back onto its linked `manta.FileJob` (`_sync_manta_job`).
- **One access policy.** `fileservices.access.user_can_access_vault_file()` is the canonical read check (superuser / owner / public / bucket owner / directory whitelist); manta re-uses it rather than trusting its caller.

### fileservices — the file-service run framework

The generic engine every media operation flows through.

- **`FileServiceRun` (models.py)** records one operation: `service_key`, `owner`, `input_file` (`vault.VaultFile`), a denormalised `bucket` (kept so stats survive input deletion), free-text `args`, `status` (pending/running/success/failed), captured `stdout`/`stderr`, `output_file_pks` (JSON list, resolved to live `VaultFile`s via the `output_files` property), an optional link to a `workflows.WorkflowRun`, and the Celery `task_id`.
- **`FileServicePlugin` (plugin.py)** is the extension point, built on `toto.core.plugin.BasePlugin`. Subclasses declare `accepted_file_types`, UI hints, and either implement `execute(run)` or set `builder = True` and return a `builder_url()`. `listed = False` keeps a service registered for programmatic use while hiding it from the file menu. Plugins are auto-discovered from every installed app's `plugins/file_service_plugins.py` module (`FileservicesConfig.ready()` → `autodiscover_plugins`).
- **Execution (runner.py).** `execute_run(run_id)` loads the plugin, flips status to running, calls `execute()`, persists outputs, and syncs the manta job. Helpers `stage_input()` (copy vault bytes to a tempdir), `save_output()` (persist a produced file as a `VaultFile`, hashing it), `tokenize_args()` (argv split that rejects shell metacharacters — note `;` is allowed for ffmpeg filtergraphs) and `run_subprocess()` are shared by the bundled plugins.
- **Bundled plugins (plugins/file_service_plugins.py):** `ffmpeg` and `ffprobe`. Both are `listed = False` — superseded by the manta builder but still runnable directly or from a workflow.
- **Workflow integration.** `predefined_tasks.fileservice_run` registers the `fileservice_run` node entrypoint; the `ingress_fileservices` management command seeds the `fileservices-run` workflow (one `PREDEFINED_TASK` node) that `dispatch_run` wraps runs in.
- **Views/URLs (`app_name="fileservices"`):** `services_for_file` (JSON menu of applicable services), `open_primary` (jump straight to a file's primary tool — video/audio route to `manta`), `run_service` (create + dispatch, or redirect to a builder), `run_detail` and `run_status` (poll a run).

### manta — ffmpeg/ffprobe command builder

An interactive builder that turns UI choices into audited argv and runs them.

- **Models (models.py):** `FileJob` is a minimal single-record job — `command` key, `owner`, `celery_task_id`, `status`, `inputs` (VaultFile id list), `params`, and a JSON `output` holding the full serialized result (including produced `files`). `MediaJob` is a proxy over `FileJob` exposing `primary_input`/`extra_inputs`.
- **Command registry (commands/).** Each operation is a small `BaseCommand` subclass that self-registers by `key` on import (`commands/__init__.py` fixes the dropdown order). A command declares input/output slots, a param form, and either a `build_spec()` (ffmpeg/ffprobe) or a `describe()` + `service_key` (service-backed). Bundled commands: compress, resize, crop, change_fps, cut, extract_mp3, remove_audio, replace_audio, add_subtitles, add_watermark, thumbnail, gif, vstack, hstack, concat, probe, transcribe — grouped into builder tabs `ffmpeg` / `ffprobe` / `transcribe`.
- **argv builders (builders.py)** are pure functions returning `list[str]`, never a shell string. Quality presets map to CRF values; progress flags are injected by the runner.
- **Execution backends (commands/backends.py).** `FfmpegCommand`/`FfprobeCommand` stage inputs into `MANTA_WORK_ROOT` (symlink, else copy), run each argv via `subprocess` with `validate_argv` guarding shell tokens, then save outputs as `VaultFile`s and serialize the result (ffprobe JSON is parsed into `output["probe"]`). `ServiceCommand`/`WhisperCommand`/`TranscribeCommand` instead create a fileservices `FileServiceRun` and dispatch it — so transcription shares one code path.
- **`FFmpegClient` (client.py)** is a lower-level runner that streams `-progress pipe:1` output and reports percentage via a callback (used where live progress is needed).
- **Vault-wand entry (plugins/file_service_plugins.py).** A single `manta` `FileServicePlugin` with `builder = True` is the menu entry for video/audio; selecting it redirects to the command builder (`manta:command_builder?file=<pk>&service=manta`). It's registered only when manta is installed, so the wand never shows a dead link.
- **Access (access.py)** re-implements the vault read policy independently (delegating the per-file check to `fileservices.access`) and exposes `accessible_bucket_files()` for validating extra-input references.
- **Views/URLs (`app_name="manta"`):** `command_builder`, `quick_transcribe`, `job_detail`, `job_status`. Direct execution runs through the `run_direct_job` Celery task (`tasks_direct.py`).

### transcription — speech-to-text data & engine layer

The domain model, services and Whisper engines behind transcription. This portion of the wheel is backend-focused: it ships models, services, admin, engine backends, Celery tasks and the fileservices bridge (no `urls.py`/`views.py`/templates live here — the interactive collection/source UI referenced by the models' `reverse()` targets is wired at the host).

- **Models (models.py).** `TranscriptCollection` (owner, vault `bucket`, `access_mode` public/private with `readers`/`writers` M2M, and `user_can_read`/`user_can_write` helpers) → `TranscriptSource` (backed by an unencrypted audio/video `VaultFile`, validated in `clean()`) → `TranscriptionJob` (an `engine` choice, language, `translate_to`, `detect_speakers`, status, `celery_task_id`, `raw_response`). Results land in `TranscriptSegment` (timestamped `start_ms`/`end_ms`, text, optional `confidence`) and `TranscriptSpeaker` (optionally linked to `people.Person`). `TranscriptArtifact` records exported files (TXT/JSON/SRT/VTT/summary) as vault files; `TranscriptEvent` captures analytics (impression/play/transcribe/export/download). `SpeechModel` is a DB registry of configured Whisper models — weights arrive by admin upload or automatic download, with `inference_path` resolving a faster-whisper zip (extracted dir), an openai-whisper `.pt`, or a size-name fallback.
- **Engines (services.py, backends.py).** `run_backend()` selects an engine per job: **openai-whisper**, **faster-whisper** (CTranslate2; CPU INT8 or CUDA), a **command** backend (external process printing JSON), a **custom callable**, or the **sidecar** dev backend (`backends.sidecar_txt_backend`, reads `<media>.txt`) for UI testing without a model. Local Whisper supports transcription and translation **to English only** (`translate_to = en`); other targets raise. Exporters `transcript_to_{text,srt,vtt,json}` and `export_transcript()`/`save_transcript_artifact()` produce the download formats.
- **Bridge to manta/fileservices (plugins/file_service_plugins.py).** A `transcription` `FileServicePlugin` (`listed = False`, accepts `audio`) runs `transcribe_demo_file()` and writes a `.txt` transcript plus, when segments are timestamped, an `.srt` — this is what the manta transcribe command dispatches.
- **Tasks (tasks.py):** `run_transcription_job` and `download_speech_model` (Celery).
- **Configuration (Django settings).** Engine selection and tuning: `TRANSCRIPTION_DEFAULT_ENGINE`, `TRANSCRIPTION_WHISPER_MODEL`, `TRANSCRIPTION_FASTER_WHISPER_DEVICE`, `TRANSCRIPTION_FASTER_WHISPER_COMPUTE_TYPE`, `TRANSCRIPTION_FASTER_WHISPER_BEAM_SIZE`, `TRANSCRIPTION_OPENAI_WHISPER_DEVICE`, `TRANSCRIPTION_WHISPER_DOWNLOAD_ROOT`, `TRANSCRIPTION_WHISPER_LOCAL_FILES_ONLY`; pluggable backends `TRANSCRIPTION_BACKEND` / `TRANSCRIPTION_COMMAND`; and `TRANSCRIPTION_ALLOW_USER_COLLECTIONS`. Per-model device/compute/beam settings can instead live in the DB on `SpeechModel`.

### vod — in-browser media playback

`vod` no longer defines its own models (the earlier VOD/HLS tables were dropped in migration `0002`); it now acts **solely as a vault play-plugin host**.

- **Play plugins (plugins/vault_play_plugins.py).** Registers `VideoVaultPlayPlugin` (`file_type="video"`) and `AudioVaultPlayPlugin` (`file_type="audio"`) via `toto.vault.plugins.VaultPlayPlugin`, each returning the `vod:vault_file_play` URL — this is how the vault knows how to "Play" a media file.
- **View/URL (`app_name="vod"`).** `vault_file_play` renders `vod/vault_file_play.html` for a `VaultFile`: encrypted files are refused; public files play openly; private files require authentication and either ownership or directory access, then play from the file's public URL.

---

## Usage

`toto-media` is a set of Django apps, not a standalone program. Install the wheel (see below), then enable the apps you need in the host's `settings.py` and include their URLs.

### Install the apps

```python
INSTALLED_APPS += [
    "toto.fileservices",
    "toto.manta",
    "toto.transcription",
    "toto.vod",
]
```

Wire the URLs (as needed):

```python
urlpatterns += [
    path("fileservices/", include("toto.fileservices.urls")),
    path("manta/",        include("toto.manta.urls")),
    path("vod/",          include("toto.vod.urls")),
]
```

Apply migrations and seed the file-service workflow:

```bash
python manage.py migrate
python manage.py ingress_fileservices   # seeds the 'fileservices-run' workflow
```

### System / Python prerequisites

- **FFmpeg** must be on `PATH` for manta and the ffmpeg/ffprobe file services (e.g. `apt-get install ffmpeg`).
- **Transcription** needs a local Whisper engine — install whichever you configure:

  ```bash
  pip install -U openai-whisper faster-whisper
  ```

  `openai-whisper` also requires FFmpeg on `PATH`; `faster-whisper` uses PyAV and usually does not.

### Running work

- For background execution, run a Celery worker against the host's broker; otherwise `dispatch_run()` executes runs inline. Media jobs are long, so a worker is recommended in production.
- Set `TRANSCRIPTION_DEFAULT_ENGINE` (and the matching `TRANSCRIPTION_*` settings above), or register a `SpeechModel` in the admin and mark it active. For UI testing without a model, point `TRANSCRIPTION_BACKEND` at `toto.transcription.backends.sidecar_txt_backend`.

### Developing against it

The apps follow the standard toto plugin pattern. To add a media operation, drop a `FileServicePlugin` subclass in an app's `plugins/file_service_plugins.py`, or add a `BaseCommand` subclass under `manta/commands/` and register it in `commands/__init__.py`. Keep argv construction in pure builder functions returning `list[str]` and never build shell strings — `validate_argv`/`tokenize_args` reject shell metacharacters by design.

---

## Build & packaging

`toto-media` is one of nine lockstep-versioned wheels in the toto suite, all sharing the `toto.*` PEP 420 namespace (`toto-ai`, `toto-base`, `toto-chat`, `toto-flow`, `toto-geo`, `toto-graph`, `toto-media`, `toto-ops`, `toto-works`). The suite ships at a single version — currently **1.6**, held in `toto_libs/VERSION` — and every package pins its siblings at that exact version. Its declared sibling dependencies are **`toto-base`** (vault storage, core plugin registry, UI shell) and **`toto-flow`** (the workflows/Celery engine); runtime app labels such as `vault`, `workflows`, `people` and `auth` are provided by the host's installed apps.

- Versions are rewritten only by `scripts/release.py` — never edit them by hand.
- Wheels are built with `scripts/build_wheels.py`; the package graph (disjoint ownership of `src/toto/*`) is enforced by `scripts/check_package_graph.py`.
- Hosts select and pin the suite in their `requirements.toto.txt` (exact pins, all at the same version); only media-capable hosts install `toto-media`.

For the full build, versioning and release manual, see the toto_libs root `README.md`.
