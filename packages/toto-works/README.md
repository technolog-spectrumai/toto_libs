# toto-works

**toto workflow-backed file, document and data services.** `toto-works` is one wheel in the lockstep-versioned toto suite. It bundles three Django apps that sit on top of the shared vault and workflow engine: **antaresia** (run Python scripts stored in the vault), **kanban** (project / mission / task management with sprint metrics), and **memo** (a browser-based slide presentation viewer and editor). Two of the three apps are thin, database-light layers over the vault; kanban is the data-rich one. All three ship under the shared `toto.*` PEP 420 namespace and are versioned in step with the rest of the suite.

## What it does (functional)

### Run Python from the vault (antaresia)
Open a Python file that lives in your vault, edit it in a browser code editor with live collaborative sync, and run it on the server with one click. Each run streams back its standard output, standard error, and exit code, and the file keeps a history of its recent runs so you can compare results. Encrypted files must be decrypted before they can run, and execution needs a background worker to be online.

### Plan and track work on boards (kanban)
Create a **Project** with a designated lead, then break the work down: group initiatives into **Campaigns**, each holding **Missions**, each holding **Tasks**. Arrange tasks across board **Columns** (your own workflow states), size them with Fibonacci weights, set due dates, and assign each to a practitioner plus an optional reviewer who signs it off. Run time-boxed **Sprints**, prioritise missions on an Eisenhower urgency/impact matrix, and attach rich how-to documentation pages to a mission. Built-in dashboards report sprint burndown, velocity, lead time, backlog health, and progress per assignee and per campaign. A JSON API lets external clients list projects, create and edit tasks, move tasks forward or back through the board, and pull mission, backlog, matrix, and sprint-metrics data.

### Build and present slide decks (memo)
Author self-contained presentations entirely in the browser — no database, no separate asset store. Start a new deck from the presentations workspace or the vault's *New File* menu, then edit it either as structured slides (paste HTML, drop in images that are automatically resized and embedded, inline SVG) or directly as raw XML source. Present the result as a full reveal.js slideshow. Because a presentation is just a file in the vault, the vault's **Play** and **Edit** buttons launch the viewer and editor, and normal vault sharing/visibility rules apply.

## How it works (technical)

The package contains three Django apps under `src/toto/`: `antaresia`, `kanban`, and `memo`. Each is a standard `AppConfig` (`toto.antaresia`, `toto.kanban`, `toto.memo`). Cross-app model relationships are expressed as string-based Django foreign keys (e.g. `"vault.VaultFile"`, `"workflows.WorkflowRun"`, `"locations.Zone"`) and are resolved when the full portal Django project is assembled; the only *declared* wheel dependencies are `toto-base` and `toto-flow` (see Build & packaging).

### antaresia — Python execution service

The single model, `PythonRun` (`antaresia/models.py`), records one execution: a required FK to `vault.VaultFile` (`CASCADE`), an optional FK to `workflows.WorkflowRun` (`SET_NULL`, the toto-flow coupling), a Celery `task_id`, a `status` (`pending` / `running` / `success` / `failed`), captured `stdout` / `stderr` / `exit_code`, and start/finish timestamps. Rows are ordered newest-first.

- **Editing.** `FileDisplayView` extends `toto.editor.BaseFileDisplayView` (`ace_mode="python"`, `ws_path="antaresia"`) and surfaces the last 10 runs for the file; `save_file` / `delete_file` are re-exported from `toto.editor.views`. Live collaborative editing is provided by `FileSyncConsumer` (`consumer.py`, `routing.py`), a `BaseFileSyncConsumer` subclass with `room_prefix="antaresia_file"` served at `ws/antaresia/file/<pk>/`.
- **Running.** `run_python` (`views.py`) creates a `PythonRun`, then prefers a workflow-backed path: if a `Workflow` with slug `antaresia-run-python` exists, it wraps the run in a `WorkflowRun` (input `{vault_file_pk, run_id}`) and dispatches `start_workflow_run_task` from toto-flow; otherwise it falls back to the plain Celery task `run_python_task` (`tasks.py`). It returns 403 for encrypted files and 503 when no Celery worker is available (`toto.celery_utils.celery_available`).
- **Execution.** Both paths call `run_python_script` (`run.py`), which reads the file, writes it into a fresh `TemporaryDirectory`, and runs it with `subprocess.run([sys.executable, script.py])` under a 30-second timeout, returning `(stdout, stderr, exit_code)`. `predefined_tasks.antaresia_run_python` registers the workflow node via `toto.workflows.predefined_tasks.register`; `apps.ready()` imports that module so the node is registered on startup.
- **Vault wiring.** `plugins/vault_editor_plugins.PythonEditorPlugin` registers `file_type="python"` so the vault's editor button routes `.py` files to `antaresia:file_display`.
- **URLs.** `file/<pk>/` (display), plus `save/`, `delete/`, `run/`, `history/`, and `run/<run_id>/status/` (JSON polling of a run). `PythonRun` is registered in the admin.

### kanban — project / mission / task management

Domain models (`kanban/models.py`) extend `toto.core.domain.DomainEntity`, except `ProjectCommitment`, which is a plain model.

- **`Project`** — `name`, `description`, `project_lead` (FK `people.Person`).
- **`Column`** — a board state within a project: `project`, `name`, `position`, `can_add_task`, and an `auditors` M2M to `Practitioner` (who may move tasks into the column). Carries `graph_node_type = "TaskStatus"`.
- **`Campaign`** — `project`, `name`, `description`, `start_date` / `end_date`, `owner` (Person), `metadata` (JSON), `zone` (FK `locations.Zone`).
- **`Mission`** — `campaign`, `title`, `description`, `urgency` and `impact` on a 1–3 scale (`THREE_SCALE`), `location` (`locations.Address`), `route` (`locations.Route`), `owner`, `metadata`. Exposes `urgency_label` / `impact_label` and `effective_zone` (its campaign's zone).
- **`Sprint`** — `name`, `project`, `start_time`, `end_time`.
- **`Practitioner`** — a professional profile for a `people.Person`, independent of any single project: `role` (`contributor` / `reviewer` / `auditor` / `manager` / `observer`), `is_active`, `work_description`, `metadata`.
- **`ProjectCommitment`** — the join that actually binds a practitioner to a project: `practitioner`, `project`, `hours_per_day`, `is_active`, date range, `metadata`, with a uniqueness constraint on `(practitioner, project)`.
- **`Task`** — `mission`, `column`, optional `sprint` (`SET_NULL`), `title`, `description`, `assignee` and `reviewer` (both FK `Practitioner`, `SET_NULL`), `due_date`, `position`, `weight` on a Fibonacci scale (1/2/3/5/8, `FIB_SCALE`), `metadata`, `completed_at`. Its `clean()` enforces that the chosen column and sprint belong to the same project as the task's `mission → campaign → project`.
- **`DocumentationPage`** — extends `verbena.AbstractPage`, one-to-one with a `Mission`, with an `is_manual` flag; **`DocumentationSection`** extends `verbena.AbstractSection` and belongs to a page.

**Metrics.** `metrics.py` provides `SprintMetricsCalculator` and `MissionMetricsCalculator`, which compute the burndown, velocity, lead-time, per-assignee, and per-campaign figures consumed by both the HTML dashboards and the API.

**Views and API.** `views.py` holds the HTML views (project list/detail, Eisenhower matrix, backlog, mission detail, task create/update/delete, promote/demote, sprint metrics, documentation page). `api_views.py` exposes a JSON API used by mesh clients: read/create endpoints extend `MeshGatedApiView` and task detail/promote/demote extend `CorsApiView` (both from `toto.api.cors`); all require an authenticated user. Project visibility is scoped by `_get_user_projects` (you must be the lead or hold an active `ProjectCommitment`). Creating a task via the API auto-provisions a default campaign and mission if the project has none. Promote/demote move a task to the next/previous column by `position`, returning HTTP 400 at the first/last column. Routes live in `urls.py` under `api/…` (projects, tasks, missions, backlog, sprint-metrics, matrix) alongside the HTML routes.

**Extensibility.** `apps.ready()` autodiscovers `plugins.kanban_plugins`; the `plugins/` package defines mission, mission-tab, and task extension points. The app also ships `forms.py`, `sync_adapters.py`, `templatetags/`, and a `tests_api.py` suite.

**Couplings.** `people` (Person), `locations` (Zone / Address / Route), `verbena` (documentation base classes), `core.domain` (DomainEntity), `api.cors` (API base views), and `socialhub` (profile links in templates). Economy/tokenisation concerns that once lived here (mission economy, project tokenisation) now live in `toto.mission_economy` and are **not** part of this package.

### memo — presentation editor, player and export

memo has **no database models of its own** (`models.py` documents that the former
`Tag` / `MemoDiagram` / `MemoDeck` / `MemoCard` models were dropped in migration
`0002_drop_memo_models`). A presentation is one self-contained XML document
stored as a `VaultFile` with `file_type="presentation"` — images embedded as
base64 `data:` URIs, SVGs inlined verbatim, nothing external to lose.

See `src/toto/memo/README.md` for the full picture. In brief:

- **Format v2.** `presentation_format.py` parses a `<presentation version theme>`
  root of `<slide layout>` elements, each a `<title>` plus typed `<block>`
  children (`heading`, `text`, `list`, `image`, `svg`, `code`, `quote`, and
  `html` as the v1 escape hatch). v1 documents — a title and one blob of body
  HTML — upgrade **in memory** to a single `html` block and are **not rewritten
  on open**, so an old deck presents byte-identically until its owner saves.
  Unknown attributes, block types and child elements are preserved and re-emitted
  rather than dropped, which v1 did not do.
- **One stylesheet, four surfaces.** `static/memo/slide.css` defines how a slide
  looks and is loaded by the editor canvas, the player, the PDF export and the
  gallery thumbnail. Reveal's own themes are deliberately *not* loaded — they put
  their variables on `:root`, which cannot work in a gallery showing several
  decks with different themes. Reveal still supplies transitions, controls and
  hash navigation.
- **The editor** (`memo:edit`) is a block canvas: the real slide at 1280×720,
  scaled to fit, with `contenteditable` text, a selection popover, layout
  presets, undo/redo, autosave with optimistic-concurrency 409s, and hand-rolled
  pointer-event drag and drop for slides, blocks, vault media and desktop files.
  All of its logic lives in `static/memo/*.js`.
- **Detection.** `file_type="presentation"`; the `.pml` extension stays retired
  and deck files are ordinary `.xml`. The type exists because the vault plugin
  registries are `dict[key -> plugin]` and only fire when `key == file_type` —
  with decks typed `xml` (already claimed by `toto.editor`) they could not have a
  Play button at all. Legacy `xml` rows are retyped the first time memo opens one.
- **URLs** (`urls.py`): `memo:index` (gallery, paginated, real thumbnails),
  `memo:create`, `memo:import`, `memo:present`, `memo:edit`, `memo:save`,
  `memo:export_pdf`, `memo:export_zip`, `memo:media_embed`, `memo:media_upload`.
  The raw-XML source editor is **retired**; its gitvault toolbar moved onto the
  editor.
- **Export.** PDF via WeasyPrint, lazily imported and gated by
  `BUILD_WEASYPRINT` (the shape `toto.notarius.render` uses); ZIP as
  `presentation.xml` plus `assets/` real files, re-importable, always landing as
  a new deck.
- **Trust.** Everything is sanitised server-side in `sanitize.py`, on save *and*
  on open — a deck can arrive by upload, and a public one renders in every
  visitor's gallery. `code` blocks are kept literal and escaped at render; `html`
  blocks stay verbatim in the player and render escaped in the gallery.
- **Static.** memo is the **first app in this package to ship static files**, at
  `static/memo/`. `pyproject.toml` package-data and `MANIFEST.in` already allow
  `.js`/`.css`; note the allowlist is by extension and that `build_wheels.py
  --sdist` builds the wheel from the sdist.
- **Tests.** `testing/settings.py` makes the suite runnable, and zenobia's
  clean-env gate runs it against the installed wheels.
- **Dependency.** `vault` (the file and the play/editor plugins), `editor` (the
  gitvault toolbar context).

### Cross-cutting design notes
- **File-as-source-of-truth.** antaresia, memo and primula store no user content of their own beyond run records: antaresia's `PythonRun` points at a vault file, and memo and primula keep everything in the vault file itself. All integrate with the vault through its plugin system rather than owning storage.
- **Workflow-backed, with a fallback.** antaresia runs scripts through a toto-flow `WorkflowRun` when the corresponding `Workflow` is configured, and degrades to a direct Celery task otherwise — the reason `toto-flow` is a hard dependency of this package.

## Usage

These apps are normally consumed as part of an assembled toto portal, not standalone.

**Install.** Hosts pin the whole suite at one version in `requirements.toto.txt`; `toto-works` is installed at the suite version alongside its siblings — see `VERSION` at the root of the vendored tree, which `scripts/release.py` is the only thing allowed to change. For local/dev work it is installed from the monorepo (editable install of the package under `toto_libs/packages/toto-works`).

**Wire it into a project.** Add the apps you need to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "toto.antaresia",
    "toto.kanban",
    "toto.memo",
]
```

Include their URLconfs (`toto.antaresia.urls`, `toto.kanban.urls`, `toto.memo.urls`), run migrations (`python manage.py migrate`), and for antaresia also register its Channels routes (`toto.antaresia.routing.websocket_urlpatterns`) and run a Celery worker — script execution and the workflow/Celery path both require it.

**Import** under the shared namespace, for example:

```python
from toto.kanban.models import Project, Mission, Task
from toto.antaresia.models import PythonRun
```

**Run the tests** against a host portal, e.g.:

```bash
cd portal && python manage.py test toto.kanban.tests_api
cd portal && python manage.py test toto.antaresia toto.memo
```

## Build & packaging

`toto-works` is part of the lockstep-versioned toto suite: all nine wheels share a single `VERSION`, kept in `VERSION` at the root of the tree and rewritten only by `scripts/release.py`. It declares its sibling pins in `pyproject.toml`:

- `toto-base` at the suite version
- `toto-flow` at the suite version — antaresia foreign-keys `workflows.WorkflowRun`

Versions are rewritten only by `scripts/release.py` (never edited by hand), and `scripts/check_package_graph.py` enforces that each wheel owns a disjoint slice of the `toto.*` namespace. The build backend is setuptools with namespace package discovery under `src/`; package data bundles `templates/**/*`, `static/**/*`, and `graph/*.yaml`. Hosts pin the assembled suite in `requirements.toto.txt`.

For the full build and release manual, see the repository root README.
