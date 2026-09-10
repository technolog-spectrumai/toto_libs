# toto.ambrosia

The shared workspace BASE: a file tree over a vault bucket, tabbed ACE editors,
and the room UI. Since the 1.46 split ambrosia owns no runtime of its own — the
language apps plug into it through `registry.WorkspaceApp` from their
`AppConfig.ready()`: `toto.antaresia` brings the Python side (a Jupyter kernel
that remembers what you did last time), `toto.texlab` the LaTeX side
(whole-bucket compiles on the worker). The dependency is one-way — specialised
app → base, never the reverse. All three are host portions of the **placidia**
host (they moved there from zenobia in 8/2026, with the rest of the
binary-heavy tier).

## Purpose

A workspace **is a folder inside a vault bucket you already own**. Ambrosia owns
no file storage of its own and creates no buckets — every file is an ordinary
`vault.VaultFile`, so it shows up in the vault browser, counts against the same
quota, and can be moved, downloaded or encrypted with the tools that already
exist. What Ambrosia adds is the framing: a VS Code-shaped room with an explorer
down the left, tabbed ACE editors, and a console underneath that keeps its
output.

You pick the bucket when you create the workspace, then either pick a folder in
it or type a name for a new one. One bucket can hold as many workspaces as you
like.

**The bucket is the project.** The tree walks the whole bucket, and every file
and folder endpoint resolves ids by `bucket_id`. This reversed an earlier
decision, deliberately: scoping to the workspace *folder* did fix a real leak —
one workspace reading a neighbour's files by pk — but at the wrong level. A
`preamble.sty` at the bucket root was then invisible in the tree **and never
staged**, so `\usepackage{preamble}` failed with no clue why. The bucket is the
thing you own; a workspace is a view onto it rooted at a folder.

The hard boundary is therefore the bucket: a file in another bucket is not in the
tree and 404s by pk. Two smaller scopes survive inside it, and both are narrower
on purpose:

- **`destroy_workspace` still uses `workspace.directory_ids()`** — the destroy
  button deletes the workspace's own folder and must never gain the power to
  empty a bucket.
- **`set_main_file` still checks the workspace** — "which file is this
  workspace's document" is a smaller question than "what may this workspace
  read", and a neighbour's `main.tex` is refused with a sentence.

(The name `antaresia` once belonged to a retired single-file Python runner in a
library wheel; the 1.46 split reclaimed it for the Python language app beside
this base. The vault editor plugin keys are still exclusive — `toto.antaresia`
claims `python`, `toto.editor` claims `latex`, and `BasePlugin.register` raises
on a duplicate — so each app's `checks.py` turns a stale-wheel shadow into a
`manage.py check` error rather than a crash at import time.)

## Screens

| Screen | What it is |
|---|---|
| **Lobby** (`/ambrosia/`) | Your workspaces, and the form that creates one. With no buckets it points you at the vault instead. |
| **Room** (`/ambrosia/w/<slug>/`) | Explorer, tabs, editor, console — and for LaTeX, a PDF preview beside the editor. One page: switching files never navigates. |

## Models

- **`Workspace`** — `name`, `slug`, `owner`, `kind` (`python` or `latex`),
  `execution` (`kernel` or `celery`, the flag a later part acts on),
  an FK to the `vault.Bucket` it lives in and a OneToOne to its root
  `vault.VaultDirectory`. Both are `CASCADE`: deleting the folder in the vault
  deletes the workspace row, because a workspace without its folder is nothing.
  Removal from inside Ambrosia is two separate buttons — see below.
- `directory_ids()` returns the root plus every descendant, in one flat query and
  a walk in Python. It is the scope for the tree and for every id lookup.
- **`Workspace.main_file`** — which `.tex` compiles, for a LaTeX workspace.
  `SET_NULL`, so deleting the main file does not delete the project.
- **`KernelSession`** — one live interpreter: `connection_info`, `pid`, `status`,
  `execution_count`, `last_used_at`. A LaTeX workspace never has one.
- **`LatexRun`** — one compilation: `status`, `log`, `error`, `passes`, the
  `pdf` it produced (`SET_NULL`), the artifact names, `task_id` and
  `workflow_run_id`. The row exists before a worker touches it, so the browser
  has something to poll and a failure has somewhere to be recorded.

`workflow_run_id` is a **plain integer, not a ForeignKey**. A real FK to
`workflows.WorkflowRun` would make `toto.workflows` a hard requirement of
ambrosia importing at all (Django `fields.E300`), and ambrosia is deliberately
outside `toto.features`. The cost is that the admin cannot follow the link; the
benefit is that a Python workspace still works on a build with no workflows.

## Services (`services.py`)

| Function | What it does |
|---|---|
| `create_workspace(owner, name, bucket, directory, new_directory_name, kind)` | Adopts `directory`, or creates `new_directory_name` inside it (or at the bucket root). Refuses a bucket you do not own, a folder from another bucket, neither-folder-nor-name, and a folder that is already some other workspace's root. Seeds `main.py` only into an empty folder |
| `close_workspace(workspace, user)` | Forgets the workspace. The folder and every file stay in the vault |
| `destroy_workspace(workspace, user)` | Deletes the whole folder: files first, then the directory. Returns the counts |
| `create_file(workspace, user, filename, directory)` | Extension decides the vault file type |
| `create_directory(workspace, user, name, parent)` | |
| `rename_file(...)` | Moves the file type with the extension — otherwise a rename lies about which editor opens it |
| `read_file` / `write_file` | UTF-8 only; refuses encrypted files and honours `file_edits_allowed()`. `read_file` returns `(text, truncated)` and caps at 512 KB, keeping the tail |
| `compile_workspace(workspace, user)` | Compiles the main document and files every artifact into `build/`. Called by the runner, not by a view |
| `artifact_directory(workspace, user)` | The `build/` folder, made on demand |
| `require_local_bucket(workspace)` | Refuses an `s3` or `remote_toto` bucket with a sentence |

## The tree

`filetree.flatten(workspace)` returns a pre-ordered depth-first list where every
row carries a `depth` integer and a parent id. That is vault's idiom and the good
part of its 2,200-line browser: indentation is padding, visibility is a parent
walk in Alpine, and arbitrary nesting costs nothing. Two queries regardless of
tree size: the bucket's directories, the bucket's files.

The walk starts at the **bucket root**, so loose files sitting beside the folders
appear at depth 0 and the workspace's own folder is a row like any other.

Rows are marked `editable`, `runnable` and `readonly` so the sidebar can grey out
what it cannot open and italicise what it must not save. Files whose type
Ambrosia does not edit — images, PDFs, archives — are still **listed**, because a
workspace that hid its own data files would be lying about what it contains.

## Closing versus destroying

Two buttons in the workspace's danger zone, because "I am done with this project"
and "delete my code" are different sentences:

- **Close** drops the `Workspace` row and nothing else. The folder and every file
  remain in the vault, and the folder can be adopted by a new workspace later.
- **Destroy** deletes the root folder, every subfolder, and every file in them.
  It is typed-confirmation only: you retype the workspace's name.

`destroy_workspace` deletes **files before directories**, and that order is not
cosmetic. `VaultFile.directory` is `SET_NULL`, so deleting the directory first
would not delete the files — it would spill them, unparented, into the bucket
root, which is exactly the mess the button exists to avoid.

## LaTeX workspaces

A workspace's `kind` decides what "run" means. Python gets an interpreter and a
Run button; LaTeX gets `pdflatex` and a Compile button, in the same room, through
the same console transcript.

### Compiling

`latex.compile_source()` writes the **bucket** into a scratch directory *keeping
its shape* — every path relative to the bucket root — then runs the compiler from
the folder that holds the main document. That is the difference from studio's
`texlab`, which gathers companion files from a single flat directory and
therefore cannot resolve `\input{chapters/intro}`. `TEXINPUTS` also points at the
whole staged tree, so `\usepackage{preamble}` finds a `.sty` sitting anywhere in
the bucket.

**latexmk when it is on PATH**, which already knows the whole dance: it reruns
until cross-references settle, calls bibtex/biber when there are citations and
makeindex when there is an index. Where it is missing the explicit ladder still
runs — up to three pdflatex passes, stopping as soon as pdfTeX stops asking for
another, with `bibtex` between them when the log reports undefined citations.
`passes` means the same thing on both paths: it is counted off the pdfTeX banner,
so a simple document costs one and a document with `\ref` costs two either way.

**A LaTeX error is a result, not an exception.** The run finishes `failed` with
the log attached, because the log is the thing the user needs. Only a missing
toolchain, or a bucket this machine cannot compile from, raises.

Compiling requires `bucket.storage_backend == "local"`. `VaultFile` bytes always
land in `MEDIA_ROOT` today whatever a bucket says, so this is a semantic guard
rather than a physical one — but an `s3` bucket is a statement that its contents
live elsewhere, and quietly compiling a local shadow of it would be a lie that
only shows up as missing figures.

### The sandbox

LaTeX is a programming language with file access, so the compile is confined
rather than trusted:

- `openin_any=p` / `openout_any=p` — TeX may not read or write outside the
  scratch tree. Without this, `\input{/etc/passwd}` typesets the file into the
  PDF and hands it to whoever asked for the compile.
- `-no-shell-escape`, passed explicitly rather than relied on as a distribution
  default.
- Names are re-sanitised while staging. Ambrosia refuses a `/` in a name it
  creates, but a workspace can adopt a folder of files that arrived some other
  way, so a `../..` in a title cannot be assumed impossible.
- Compiling requires the same `can_execute` grant as running Python. It is
  execution; it merely looks like typesetting.

### The main document

A project holds many `.tex` files and only one of them compiles. `main_file`
records the choice — "Set as main document" in the explorer's context menu — and
`latex.resolve_main()` falls back to convention until someone makes one:
`main.tex` at the root, then the first `.tex` at the root, then the first `.tex`
anywhere. A `main_file` that has been moved out of the workspace is ignored
rather than obeyed.

### The output, and the loop it would otherwise create

**Every file the run produced** — the PDF, the `.log`, `.aux`, `.toc`, `.bbl` —
is written back into a `build/` folder beside the workspace root, one
`VaultFile` each, overwriting the previous run's rather than accumulating
`main-2.pdf`. A compile is not history: a document compiled fifty times should
not leave fifty logs behind.

The `.log` in `build/` is **our transcript**, not pdfTeX's file: it carries the
staging notes and the latexmk/bibtex output together, which is what you want when
the question is "why was my figure not found".

`build/` is excluded from staging **by directory name**, and generated extensions
are excluded by name as well. Both, because the directory rule is the real one
and the extension list catches a stray `.aux` a user drops beside their sources.
Without this the `.log` — typed `text`, and `text` is staged — is copied back in
as an **input** on the next run, and latexmk then sees a stale `.fdb_latexmk` and
skips passes. That is the loop this design exists to prevent, and there is a test
named after it.

Artifacts open in the editor and are **read-only**: you read a log, you do not
edit it. `filetree` marks the row, the room locks the ACE session, and
`file_save` refuses with a 409 — the rule is enforced on the server, because a
hand-made POST must not overwrite a compile's output either.

A `.log` is often megabytes, so `read_file` caps what it loads at 512 KB and
returns the **tail**: errors accumulate at the end of a TeX log, and refusing to
open a 40 MB file is not better than opening the end of it. The editor says which
part it is showing.

The preview is an `<iframe>` and the browser's own PDF viewer — search, zoom and
printing for free, and no 1.5 MB of pdf.js to download. Its bytes come from
Ambrosia's own `file_raw` endpoint rather than vault's public URL, so a private
project's output does not have to be made public to be looked at.

**Note:** `.tex` files in the vault browser open in `toto.editor`'s LaTeX editor,
not here. `toto.editor` registers the `latex` plugin key in `toto-base` and
`BasePlugin.register` raises on a duplicate, so Ambrosia cannot claim it — see
the `python`/antaresia collision in `checks.py` for the same rule.

### The compile is a background job

A multi-pass compile of a real document takes tens of seconds, so it does not
happen in the request. Pressing Compile creates a `LatexRun`, queues it, and
answers with a run id; the room polls `runs/<pk>/` every 1500 ms and renders the
log into the same console transcript a Python run uses.

This is `repo/dispatch.py`'s pattern: **one shared runner owns the row, and
every path calls it.**

| Module | What it is |
|---|---|
| `runner.execute_latex_run(run_id)` | Sets RUNNING, compiles, files the artifacts, sets SUCCESS/FAILED. **Never raises** — an exception would leave the row RUNNING forever with nothing in it, the one outcome the browser cannot recover from |
| `predefined_tasks.py` | `@register("ambrosia_compile_latex")`. Autodiscovered by `WorkflowsConfig.ready()`; no registration list, and no `celery_app.py` entry — that list is only for bare `shared_task` modules. Re-raises on failure so the linked `WorkflowRun` shows FAILED rather than a false COMPLETED |
| `workflow.ensure_compile_workflow()` | Get-or-creates the `ambrosia-compile-latex` workflow and restores its node if someone deletes it. Self-healing, so "the workflow row is missing" is not a state a user can get stuck in |
| `dispatch.py` | Creates the row, queues it, or **refuses with the missing piece named** |

Gitvault has a third path — run it inline when there is no worker — and ambrosia
deliberately does not. Running a 60-second subprocess in the request is the thing
this change exists to stop, so no worker means a 503 that says `BUILD_WORKFLOWS`
or "start a worker", and the view closes the run rather than leaving it PENDING
for a worker that is never coming.

The runner stays **directly callable**, which is what keeps the tests honest: the
zenobia gate has no broker, no redis and no worker, so `test_dispatch.py` and
`test_latex.py` call `execute_latex_run()` the way
`repo/tests/test_workflow_integration.py` does. What is mocked is the broker,
never the compile.

## The interpreter

`jupyter_client` starts a real IPython kernel as a child process, one per
workspace. There is no ZeroMQ hop and no `kernel_server` container:
`mandragora`'s server stays retired.

**Why the connection info is stored in the database.** A web tier runs several
worker processes, and the request that starts a kernel is usually not the request
that runs code in it, so an in-memory handle would work only by luck.
`jupyter_client` writes a connection file — but it owns that file and deletes it
when the `KernelManager` is garbage collected, which happens as soon as the
starting request returns. The kernel process outlives it; the file does not. So
`KernelSession.connection_info` holds the ports and HMAC key, and any process
attaches with `load_connection_info()`.

**Startup bootstrap.** Each kernel runs `%matplotlib inline` on start. Without
it `plt.show()` renders to a GUI window that does not exist and the console gets
`<Figure size 640x480>` as text — which looks exactly like rich output being
broken. Charts work without anyone knowing that magic exists.

**Liveness.** `_pid_alive` reads `/proc/<pid>/stat` rather than trusting
`os.kill(pid, 0)`, because that succeeds for a **zombie** — a kernel that
segfaulted looks perfectly alive to the naive check and the next execution hangs
waiting for a reply that is never coming.

**Limits, stated rather than hidden:**

- The kernel speaks over localhost, so it is reachable only from the container
  that started it. Correct for one web container; wrong if the web tier is scaled
  horizontally.
- Execution is synchronous — a long cell blocks the request until
  `AMBROSIA_EXEC_TIMEOUT` (30s), at which point the kernel is interrupted and
  usually recovers. There is no streaming.
- One kernel per workspace, not per user per workspace.
- Kernels are real processes and accumulate. `manage.py reap_antaresia_kernels`
  stops the idle ones — and celery beat now runs it every 15 minutes as
  `antaresia-reap-kernels` (`ANTARESIA_REAP=0` disables;
  `ANTARESIA_REAP_SWEEP_DEAD=1` adds the dead-pid pass, truthful only where
  kernel pids are visible). The idle limit is per workspace: the free default
  is one hour, and the workspace owner can raise it — for a daily demurrage
  fee — from the room's Workspace settings.

## The console

A transcript, not a viewport. Every execution **appends** an entry and nothing
above it is ever rewritten, so output from an earlier run stays exactly as it
was. **Clear** empties the transcript and deliberately does **not** restart the
kernel — tidying the screen and losing your variables are different actions, and
a console that conflates them is a papercut.

Rich output renders `image/png` (matplotlib), `image/svg+xml`, `text/html` and
`text/plain`. Exactly one representation is kept per result: a kernel usually
offers `text/plain` alongside a figure, and sending both would draw the chart and
then its repr underneath it.

Two ways in, both through the same path: **Run file** (saves first — running a
buffer that differs from the file on disk is the kind of confusion that costs an
afternoon), and typing at the prompt. **Run selection** was removed on
2026-09-10 with the rest of the built-in interface work; the assistant's
read/write handlers use the ACE selection API directly and were unaffected.

## Security

**This is trusted-operator code, by decision, and the word "sandbox" does not
appear anywhere in it.** A kernel is a plain Python process running as the
server: the ORM, `SECRET_KEY`, the database credentials and the media volume are
all reachable from a workspace. Nothing is contained.

So execution is gated separately from editing. `AMBROSIA_EXECUTION_ACCESS`
defaults to `"staff"`; `"superuser"` and `"authenticated"` are the other values,
and the last is only sane on a single-tenant host. Owning a workspace lets you
edit it; running code in it is a grant.

This matches what the rest of the suite already does — workflow lambdas are the
same privilege in a less inviting wrapper — but a workspace is far more inviting
than a lambda, which is exactly why the gate is here and why this section says so
plainly rather than calling it sandboxed.

## Tests

```bash
cd zenobia/zenobia && BUILD_AMBROSIA=1 BUILD_EDITOR=1 BUILD_WORKFLOWS=1 \
  DJANGO_SETTINGS_MODULE=zenobia.settings python manage.py test \
  toto.ambrosia.tests.test_workspace toto.ambrosia.tests.test_views \
  toto.ambrosia.tests.test_latex toto.ambrosia.tests.test_dispatch \
  toto.ambrosia.tests.test_kernel
```

`toto` is a PEP 420 namespace package, so the modules are named explicitly —
`manage.py test toto.ambrosia` discovers nothing. They are listed one by one in
`zenobia/scripts/clean_env_test.sh`. The tests that really run pdflatex skip
themselves where there is no toolchain; everything else runs everywhere.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `AMBROSIA_EXECUTION_ACCESS` | `"staff"` | Who may run code |
| `AMBROSIA_EXEC_TIMEOUT` | `30` | Seconds before a cell is interrupted |
| `AMBROSIA_KERNEL_STARTUP_TIMEOUT` | `60` | Seconds to wait for a kernel |
| `AMBROSIA_KERNEL_IDLE_SECONDS` | `3600` | What the reaper considers idle |
| `AMBROSIA_KERNEL_DIR` | tempdir | Where connection files are written |

## Dependencies

`toto.vault` (buckets, files, the editor plugin registry) and `toto.core`
(Platform, PageProcessor, the Oya templates). `jupyter_client`, `ipykernel` and
`matplotlib` from `requirements.realtime.txt` — `checks.py` *warns* rather than
fails when they are absent, so a build can ship the editor without the
interpreter.

`toto.workflows` and a Celery worker are needed only to **compile**, and are
imported lazily for the same reason: everything else — the tree, the editor, the
Python interpreter — works on a build that has neither.

Notably **no channels**: the console is request/reply, so unlike canasta this app
needs no WebSocket and costs `toto.features` nothing.

## Known limitations

- **A compile needs a worker, and no shipped zenobia profile has one with
  texlive.** Every profile that mentions TeX sets `INSTALL_TEXLIVE: "0"` — the
  layer left with texlab for the studio host. The builder now warns about
  exactly this rather than pretending otherwise: ticking Ambrosia brings
  `BUILD_WORKFLOWS` and the celery service, but **not** `INSTALL_TEXLIVE`, which
  stays an explicit choice because it is ~450 MB and
  `tools/test_builder.py` forbids any capability from dragging it in.
- `checks.py` raises `ambrosia.W002` when a build holds LaTeX workspaces it
  cannot compile — only then, because a check that fires on every build is a
  check people learn to ignore.
- Two workspaces in one bucket share its `build/`? No — each workspace has its
  own, beside its own root folder. But they do share every *source* file, which
  is the point and is also how one could overwrite the other's `chapter.tex`.
  The bucket is the trust boundary.
- The compile is unmetered. `technology.md` defers `ambrosia.execute`; generated
  files are unmetered everywhere in this repo.
- `Workspace.execution` is recorded but only `kernel` is wired up for Python;
  the Celery batch path for *Python* runs is a later part.
- No SyncTeX reverse search — clicking the PDF does not jump to the source.
- No git. Workspaces will reuse `repo.GitRepo` on the root directory.
- No collaborative editing yet. Pointing the tab's socket at the existing
  `ws/editor/file/<pk>/` is the intended route and needs no new consumer.
- The editor opens one file at a time in one ACE instance; tabs swap the buffer
  rather than holding an editor each.

## Next

Celery batch execution for Python runs, on the same `LatexRun`-shaped row;
removing the git decorations from vault; workspace git; SyncTeX.

## History

Written August 2026 as the successor to `toto.antaresia`, which was retired with
the `kernel_server` container in the 2026-07-28 slimming. Its code is still in
the `toto-works` wheel zenobia pins — reviving it was never a packaging problem,
only a settings one — but Ambrosia replaces rather than restores it.
