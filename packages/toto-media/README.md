# toto-media

`toto-media` is the **light** media tier of the toto suite: **"toto media over the
vault: video/audio playback and OCR."** Two Django apps under the shared `toto.*`
namespace — **vod** and **ocr** — that let a member play the audio and video
already in their vault, and pull the text out of a screenshot. Both read from vault
storage, both are stateless, and neither needs a background worker.

This is the media package hosts actually pin. Until 1.21 it was the heavy one,
bundling `manta`, `fileservices` and `transcription` as well; those moved to
[`toto-media-ops`](../toto-media-ops/README.md), which **no host pins**. The split
is what made `BUILD_MEDIA=1` safe on a lean WSGI image — it used to reach
`workflows` through `fileservices`' model FK and so force the whole celery/channels
pip layer.

**The dividing line is a worker or a heavy wheel — not a native binary.** ocr shells
out to `tesseract` and still lives here, because a small apt layer that no host is
forced to build is a different order of cost from torch, ffmpeg and a celery
container.

---

## What it does (functional)

- **Play media from the vault.** Video and audio files get a Play action that opens
  an in-page HTML5 player, access-gated exactly as the vault gates the file. A
  `/vod/` **library page** lists everything playable you can open, newest first —
  public files for anonymous visitors, plus your own and anything in a directory
  you may read once you are logged in.
- **Read text out of an image.** Load a screenshot or photo, choose its language,
  and the extracted text appears beside it. You can save the image into a vault
  bucket in the same step. Where the knowledge graph is installed, one button hands
  the text on to the ingestor; where it is not, the button is simply absent.

Nothing is transcoded and nothing is queued. Both pages do their work inside the
request.

---

## How it works (technical)

### `vod` — in-browser playback

No models and no migration files (its old pair, which created the `VodCollection` /
`VodVideo` / `VodAccessGrant` / `VodPlaybackEvent` tables and dropped them again, went
at the 2026-10-01 migrations reset), so the app is pure view code. It registers one `VaultPlayPlugin` per playable file type
(`video`, `audio`) in `plugins/vault_play_plugins.py`; the vault's Play button looks
that registry up, so an uninstalled vod just renders a disabled button.

`LibraryView` reads the playable file types **back out of that same registry**
rather than restating them, so the library and the Play button cannot drift — add a
third plugin and it joins the listing for free. Its visibility filter is
`vault_file_play`'s access rule expressed in SQL (public, or yours, or in a
directory you may read), and it deliberately omits private directory-less files
somebody else owns: the play view would 403 on every one of them, superuser or not.

### `ocr` — tesseract text recognition

Also no models. `OcrHelper` (`ocr/ocr.py`) wraps `pytesseract`, and both that import
and `PIL` are **inside the function**, so a host without the binary boots normally
and only fails when someone actually runs a scan. Saving reuses the canonical
`VaultFile` create pattern, including a `_unique_key()` retry and an
`IntegrityError` catch for the `(bucket, key)` race.

Two things about it are load-bearing and easy to undo by accident:

- **It does not imply the knowledge graph.** The ingestor handoff is soft at both
  ends: `ocr_home` wraps `reverse("ingestor:home")` *and* the ravioli import in
  `except (NoReverseMatch, ImportError)` and greys the button out, and `ocr_ingest`
  redirects back to itself on `NoReverseMatch`. Before 1.21 an `if ocr: graph = True`
  closure cost five Neo4j apps and an auto-started neo4j container for a button that
  switches itself off.
- **Its migration depends on the vault and the user model only.** Since the
  2026-10-01 migrations reset ocr has one `0001_initial`. The old
  `('workflows', '0001_initial')` edge is gone: a since-deleted `ImageTransform` had an
  FK to `workflows.LambdaFunction`, and that edge outlived the model and made
  `BUILD_OCR=1` unmigratable without `BUILD_WORKFLOWS=1` (Django raises
  `NodeNotFoundError` for a dependency on an uninstalled app). Keep it that way when
  adding a model here.

**A trashed source is a missing one** (2026-10-01). A run read from a vault file
keeps a foreign key to it, and the key still finds a file in the vault's trash (the
vault's base manager sees the trash), so a retry read its bytes as if nothing had
happened. `runs.source_path` now refuses such a file (`runs.SourceTrashed`, a
`FileNotFoundError`): a page task fails at once without reading it, the retry door
answers 409 with a sentence, and the run's page shows that sentence in place of the
"Read the missing pages" button. Restored, the file is read again. Tests:
`tests_trashed_source`.

### The shared Media sub-nav

Both pages include `oya/_media_tabs.html`, which lives in **toto-base**, not here.
That is deliberate: Django only loads template dirs for *installed* apps, so a
partial living in `vod/templates/` would vanish exactly when vod is switched off —
routine, since `BUILD_VOD` and `BUILD_OCR` are independent. Every tab is guarded
twice, by `|app_installed` and by the `{% url … as %}` form, so the bar renders two
entries, one, or none without special-casing.

It also may not live in a `templates/media/` directory: `.gitignore` carries
`media/` for MEDIA_ROOT, a bare directory rule matches at any depth, and a partial
put there is silently never committed — it renders from the source tree and ships in
no wheel. That cost a release to find.

---

## Usage

### Install the apps

Both are opt-in per host, resolved by `toto.features`:

| Flag | Installs | Default | Brings with it |
|---|---|---|---|
| `BUILD_VOD` | `toto.vod` | follows `BUILD_MEDIA` | nothing |
| `BUILD_OCR` | `toto.ocr` | off | `INSTALL_TESSERACT` |

`BUILD_MEDIA` survives as the umbrella default for `BUILD_VOD` because shipped
profiles set it and nothing else; it no longer means the processing stack. Mount the
apps with `path("vod/", include("toto.vod.urls"))` and
`path("ocr/", include("toto.ocr.urls"))`, guarded by `apps.is_installed`.

`toto.vod` forces neither `workflows`, channels nor celery, has no tasks, owns
no tables, and belongs in neither `registry.TASK_MODULES` nor a host's
`APPS_TO_SYNC`. It has nothing to migrate: no models and no migration files.

**`toto.ocr` stopped being that kind of app in 1.51.** Reading a scan is one
Celery task PER PAGE plus a nightly retention sweep, so it IS in
`registry.TASK_MODULES` and `BUILD_OCR` pulls the realtime layer in with it; and
it owns real tables now (`OcrRun`, `OcrPage`, its settings row and its metering
pair). Tesseract and poppler live in the application image rather than in an
`anastasia-ocr` runner — the argument the zenobia Dockerfile already makes about
WeasyPrint, applied to a ~20 MB layer instead of a ~450 MB one.

### System / Python prerequisites

- **vod:** none. The browser does the decoding.
- **ocr:** the `tesseract-ocr` binary plus a language pack per language you want
  (`tesseract-ocr-eng`, `tesseract-ocr-pol`, …) — the base package ships no usable
  language data, and the full `-all` set is ~800 MB — and `pytesseract` + `pillow`
  in the host's requirements.

A host that pins this wheel but declares neither the binary nor `pytesseract` is
fine as long as `BUILD_OCR` stays off, which is how zenobia carries it today.

### Developing against it

Both pages need an active `Platform` row (`PageProcessor` 404s without one) and the
`sso` url namespace mounted, since `oya/base.html` reverses into it. The library's
`tests/settings_min.py` supplies both.

The ocr suites have a harness of their own, `toto.ocr.testing.settings`
(2026-10-01): no host in the monorepo installs the app, so nothing else runs them.
From a host directory, whose `manage.py` puts the vendored sources first:
`manage.py test toto.ocr.tests_trashed_source --settings=toto.ocr.testing.settings`.

---

## Build & packaging

One of twelve lockstep-versioned wheels in the toto suite, all sharing the `toto.*`
PEP 420 namespace. The suite ships at a single version held in `toto_libs/VERSION`,
and every package pins its siblings at exactly that version.

Its **only** declared dependency is `toto-base`. The `toto-flow` dependency it
carried until 1.21 left with `fileservices`, whose FK to `workflows.WorkflowRun` was
the last thing here that needed it. Keep it that way — `tests/test_packaging.py`
asserts both that this wheel requires nothing but `toto-base` and that its app set
is exactly `{vod, ocr}`.

- Versions are rewritten only by `scripts/release.py` — never by hand.
- Wheels are built by `scripts/build_wheels.py`; disjoint ownership of `src/toto/*`
  is enforced by `scripts/check_package_graph.py`.
- Hosts select and pin the suite in their `requirements.toto.txt`, exact pins only.

For the full build, versioning and release manual, see the toto_libs root
`README.md`.
