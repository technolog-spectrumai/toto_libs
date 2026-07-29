# toto.fileservices — parked 2026-07-29 (toto v1.21)

The generic "run a service over a vault file" substrate: a `FileServiceRun` audit
row per invocation, a `FileServicePlugin` registry other apps registered into, a
dispatcher that wrapped each run in a `workflows.WorkflowRun` so it was tracked
like any other job, a runner that staged inputs and wrote outputs back as new
`VaultFile`s, and the wand button in the vault file list that started it all.

Parked together with [manta](../manta/PARKED.md) — the two are the ffmpeg half of
the platform, and they were retired as one act. See that file for the reasoning
that applies to both.

## Why it was parked

It was a substrate with nothing left on top of it.

- **Its plugin registry ran dry.** Three apps ever registered a
  `FileServicePlugin`: `transcription` (parked earlier in this same release, for
  its own reasons — see [transcription/PARKED.md](../transcription/PARKED.md)),
  `manta`, and fileservices' own. With transcription and manta gone, the registry
  holds nothing, `_has_services()` in the vault is False for every file, and the
  wand button never appears. What remained was a dispatch mechanism with no
  services to dispatch.
- **It was the reason a lean host could not be lean.** `fileservices` has a live
  FK to `workflows.WorkflowRun` and a module-scope
  `from toto.workflows.predefined_tasks import register`, so installing it forced
  `workflows`, which forced `toto.mandragora` + `jsoneditor` + the whole
  celery/channels pip layer. `zenobia_mini.yaml` said so in its own header: the
  only way to be lean was to lose the media section entirely.
- **ffmpeg went with it.** The apt layer, `INSTALL_FFMPEG` and `Features.ffmpeg`
  all existed for these two apps and nothing else. Retiring them removed a native
  binary from every image.

## What it leaves behind

**In code:** nothing that breaks. All three call sites outside the app were
already defensively wrapped, which is why parking it is a no-op for the vault:

- `vault/views.py:93` — `from toto.fileservices.plugin import FileServicePlugin`
  inside `try/except Exception` → `_fs_plugins = []` → no wand button.
- `vault/views.py:823` — the same, in `_service_stats()` → returns `[]`, so the
  bucket page's per-service run table renders empty rather than erroring.

Note both catch `Exception`, not `ImportError` — that breadth was load-bearing
even before this, because with the wheel present but the app out of
`INSTALLED_APPS` the model import raises `RuntimeError`, not `ImportError`.

`toto.gitvault` reads as though it depends on this app — its `dispatch.py`,
`runner.py`, `predefined_tasks.py` and `GitRun` docstrings all name fileservices —
but that is lineage, not coupling: gitvault was written by copying this app's
shape and imports nothing from it. It is unaffected.

**In the database:** the `fileservices_fileservicerun` table remains, untouched,
the same treatment `sketch` and `transcription` got. Orphaned, not dropped;
dropping it is a deliberate, separate act. Its rows are the run history, so they
survive a revival.

`APPS_TO_SYNC` and `INGRESS_ALLOWED_APPS` on zenobia lost their `fileservices`
entries in the same commit — a label in `APPS_TO_SYNC` for an uninstalled app
makes `backup_engine.iter_models()` raise `LookupError`, which breaks *every*
backup and restore path, not just this one.

## What was added just before it was parked

`RunListView` + `templates/fileservices/run_list.html` (an owner-scoped index of
your runs, capped at 200) were written in this same release, for the Media
sub-nav, and never deployed. The app had `run_detail` but no index before that, so
a run you navigated away from was unreachable — worth keeping if this comes back.
The `media/_tabs.html` include was stripped from that template on the way here,
since the shared sub-nav has no fileservices entry any more.

## Reviving it

1. A home in a package and a `BUILD_FILESERVICES` flag, plus `if fileservices:
   workflows = True` back in the `toto.features` closure — the FK makes that one
   mandatory, not a preference (without it Django fails with fields.E300/E307).
2. `INSTALL_FFMPEG` and the apt layer back in the Dockerfile, if any revived
   service actually shells out to it.
3. At least one plugin to dispatch. Reviving manta with it is the obvious move.
4. `"fileservices"` back in `APPS_TO_SYNC`, and `toto.fileservices` in
   `INGRESS_ALLOWED_APPS` so `ingress_fileservices` can seed the generic
   `fileservices-run` workflow that wraps each run.
