# toto-media-ops

The heavy media-processing tier of the toto suite: **ffmpeg and transcription**.
Three Django apps under the shared `toto.*` namespace — **manta**,
**fileservices** and **transcription** — that take a file out of the vault, run a
native tool over it on a background worker, and write the result back.

**`toto.ocr` is not here.** It started in this package and moved back to
`toto-media`, because it is the odd one out: no models, no celery, one small apt
package, and both of its imports inside the function it needs them in. Grouping it
with the whisper and ffmpeg stack forced hosts to choose between having OCR and
having a light image. What is left here is exactly the set that wants a worker.

> ## No host installs this package
>
> That is not an oversight, it is the package's reason for existing. Every host's
> `requirements.toto.txt` pins the suite exactly and none of them names
> `toto-media-ops`; `scripts/test_monorepo.py` in the portal monorepo asserts that
> none ever does, so a pin added by accident fails a gate rather than shipping.
>
> A wheel is still built on every release and the version still moves in lockstep
> with the rest of the suite. The apps stay importable, testable and reviewable —
> the alternative was `toto_libs/limbo/`, which is not a package at all and is
> stripped out of every host's vendored tree. This tier is heavier than the rest of
> the suite but not abandoned, and this is the shelf it sits on.

---

## Why it is uninstalled

Three reasons, and no app here escapes all three:

- **A native binary.** manta and fileservices shell out to `ffmpeg`/`ffprobe` — an
  apt layer in the image that exists solely for this tier. zenobia's Dockerfile
  carried an `INSTALL_FFMPEG` layer for years for exactly those two apps, and
  dropping this package from the pins is what let that layer go.
- **Python wheels no host ships.** transcription needs `openai-whisper` or
  `faster-whisper`, and transitively torch or ctranslate2. Those appear in no
  host's `requirements.txt`, so its backends have always raised `RuntimeError` on
  every real deployment — the feature could not have run even when it was
  installed.
- **A celery worker.** manta's `run_direct_job` has a 7200-second soft limit and
  transcription's tasks are longer still (the broker's `visibility_timeout` was
  raised to 7800s for them). Both enqueue work that queues forever without a
  worker container.

Against that, what the tier was actually delivering was thin. transcription had
**no user interface whatsoever** — no `views.py`, no `urls.py`, no templates, and
four dead `reverse("transcription:…")` calls at `models.py:89`, `:194`, `:200` and
`:448` that are the fossils of a UI removed long before. Nothing outside the app
read any of its eight models: the only reachable path,
`transcribe_demo_file()` (`services.py:604`), builds *unsaved* objects and writes
zero rows.

## What each app is, and what reviving it needs

### `manta` — the ffmpeg/ffprobe command builder

Pick a vault file, assemble a command through a form, watch the exact argv update
live, run it on a worker, get the outputs back as new vault files. A `FileJob`
model (plus a `MediaJob` proxy), 16 operations across two tabs (`ffmpeg`,
`ffprobe`), and `tasks_direct.run_direct_job`.

It is more self-contained than its old comments implied, and a revival should not
re-inherit the wrong idea:

- **It does not need `fileservices`.** Its two references are both safe with
  fileservices absent — `access.py` imports `user_can_access_vault_file`, a pure
  function with no Django imports, and `plugins/file_service_plugins.py` is only
  ever imported by *fileservices'* own `autodiscover_plugins`, i.e. the inbound
  direction. Without it manta loses the vault wand's entry point, not its page.
- **It does not need `workflows`.** It has exactly one FK, `FileJob.owner → User`;
  input and output VaultFiles are raw id lists in JSONFields. It reached workflows
  only *through* fileservices.

To revive: a `BUILD_MANTA` flag, `INSTALL_FFMPEG` back in the Dockerfile,
`services.celery` in the profile, `"toto.manta"` in `registry.TASK_MODULES` and
`"manta"` in the host's `APPS_TO_SYNC`. Add it to the **`realtime`** derivation in
`toto.features`, *not* the workflows closure — what it needs is celery, which is
what that tier's pip layer installs; `tasks_direct.py` does
`from celery import shared_task` at module scope, so the image would not boot
without it.

### `fileservices` — the run substrate

A `FileServiceRun` audit row per invocation, a `FileServicePlugin` registry other
apps register into, a dispatcher that wraps each run in a `workflows.WorkflowRun`,
a runner that stages inputs and writes outputs back, and the wand button in the
vault file list.

Its registry ran dry before it was pulled: the only three apps that ever
registered a plugin were transcription, manta and its own. It is also the reason a
lean host could not be lean — the FK to `workflows.WorkflowRun` plus a
module-scope `from toto.workflows.predefined_tasks import register` forced
`workflows`, which forced `toto.mandragora` + `jsoneditor` + the whole
celery/channels pip layer.

`RunListView` and `templates/fileservices/run_list.html` — an owner-scoped index
of your runs, capped at 200 — were written in 1.21 and never deployed. Before
them the app had `run_detail` but no index, so a run you navigated away from was
unreachable. Worth keeping.

To revive: `BUILD_FILESERVICES`, `if fileservices: workflows = True` back in the
`toto.features` closure (the FK makes that mandatory — without it Django fails
with `fields.E300`/`E307`), `"fileservices"` in `APPS_TO_SYNC`, and
`toto.fileservices` in `INGRESS_ALLOWED_APPS` so `ingress_fileservices` can seed
the generic `fileservices-run` workflow. And at least one plugin to dispatch;
manta is the obvious one.

### `transcription` — local Whisper speech-to-text

Eight models (collections, sources, jobs, segments, speakers, artifacts, events,
model weights) and ~900 lines of service code that ran openai-whisper or
faster-whisper and exported `.txt`/`.srt`/`.vtt`/`.json`.

To revive it needs, in order: the pip deps above; a `BUILD_TRANSCRIPTION` flag —
note `plugins/file_service_plugins.py` hard-imports `toto.fileservices` at module
scope and its plugin is only ever registered by `fileservices.apps.ready()`, so
`transcription → fileservices` is a real closure, not a preference; **a UI**, for
which the four dead reverses above are the spec; and `"transcription"` back in
`registry.TASK_MODULES`.

## What is left behind in a database

`manta_filejob`, `fileservices_fileservicerun` and the eight `transcription_*`
tables remain in any database that once had these apps installed — orphaned, not
dropped, the same treatment `toto.sketch` got. Rows survive a revival; dropping
them is a deliberate, separate act.

Two host-side lists had to lose their entries when the apps went, and both fail in
a way worth knowing about: a stale label in `APPS_TO_SYNC` makes
`backup_engine.iter_models()` raise `LookupError`, which breaks **every** backup
and restore path rather than just this tier's.

## What stayed in `toto-media`

`toto.vod` and `toto.ocr`, in the package hosts do pin. vod is the vault's play
button plus a library listing; ocr is a screenshot, a language and a text box.
Neither has models, a celery task, or a heavy wheel. Splitting this package off
them is what made `BUILD_MEDIA=1` viable on a lean WSGI host for the first time.

The dividing line is **a worker or a heavy wheel, not a native binary**: ocr needs
tesseract and still belongs over there, because a small apt layer nobody is forced
to build is a different order of cost from torch and a celery container.
