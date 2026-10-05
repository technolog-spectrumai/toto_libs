# toto-works

**toto file, document and data services.** `toto-works` is one wheel in the lockstep-versioned toto suite. It bundles four Django apps over the shared vault: **kanban** (project / mission / task management with sprint metrics), **memo** (slide decks), **cyprian** (a writer: long documents with real pages and a finished PDF), and **sketch** (an SVG drawing board; a drawing is an ordinary `svg` vault file). Three of the four are thin, database-light layers over the vault — memo, cyprian and sketch keep their content in one self-contained vault file each and own only metering tables; kanban is the data-rich one. All four ship under the shared `toto.*` PEP 420 namespace.

**Two apps this file used to name are gone from the package.** **primula** (vault-backed spreadsheets) was parked on 2026-09-02 and **antaresia** (the old vault Python runner) was deleted in 1.45; neither is in this tree. This file has no section for `sketch` yet. The repository root README (section 5.8) is the current description of all four apps; zenobia, the main host, installs none of them and unpinned the package on 2026-10-01.

## What it does (functional)

### Plan and track work on boards (kanban)
Create a **Project** with a designated lead, then break the work down: group initiatives into **Campaigns**, each holding **Missions**, each holding **Tasks**. Arrange tasks across board **Columns** (your own workflow states), size them with Fibonacci weights, set due dates, and assign each to a practitioner plus an optional reviewer who signs it off. Run time-boxed **Sprints**, prioritise missions on an Eisenhower urgency/impact matrix, and attach rich how-to documentation pages to a mission. Built-in dashboards report sprint burndown, velocity, lead time, backlog health, and progress per assignee and per campaign. A JSON API lets external clients list projects, create and edit tasks, move tasks forward or back through the board, and pull mission, backlog, matrix, and sprint-metrics data.

### Write documents (cyprian)
Write a long document in one WYSIWYG canvas that draws the A4 page boundaries as you type, so a page break in the editor is a page break in the PDF. Headings, lists, quotes, code, tables, images, formulas and callouts come from a toolbar; three system colours and three sizes follow the platform theme in the reader and in print. A **Source** tab holds the file itself. Save the finished thing into the vault as PDF or HTML. A document, like a deck, is one self-contained XML file — `toto.notarius` outsources contract prose to it where both are installed.

### Build and present slide decks (memo)
Author self-contained presentations entirely in the browser — no database, no separate asset store. Start a new deck from the presentations workspace or the vault's *New File* menu, then edit it either as structured slides (paste HTML, drop in images that are automatically resized and embedded, inline SVG) or directly as raw XML source. Present the result as a full reveal.js slideshow. Because a presentation is just a file in the vault, the vault's **Play** and **Edit** buttons launch the viewer and editor, and normal vault sharing/visibility rules apply.

## How it works (technical)

The package contains four Django apps under `src/toto/`: `kanban`, `memo`, `cyprian` and `sketch`. Each is a standard `AppConfig`. `cyprian` imports `toto.memo`'s sanitisers and media helpers, which is why the two share a wheel; it reaches `toto.notarius` only through `apps.is_installed` guards, so the wheel does not depend on the host that owns contracts. Cross-app model relationships are expressed as string-based Django foreign keys (e.g. `"vault.VaultFile"`, `"locations.Zone"`) and are resolved when the full portal Django project is assembled; the *declared* wheel dependencies are `toto-base` and `toto-geo` (see Build & packaging).

### kanban — project / mission / task management

Domain models (`kanban/models.py`) extend `toto.core.domain.DomainEntity`, except `ProjectCommitment`, which is a plain model.

- **`Project`** — `name`, `description`, `project_lead` (FK `people.Person`).
- **`Column`** — a board state within a project: `project`, `name`, `position`, `can_add_task`, and an `auditors` M2M to `Practitioner` (who may move tasks into the column). Carries `graph_node_type = "TaskStatus"`.
- **`Campaign`** — `project`, `name`, `description`, `start_date` / `end_date`, `owner` (Person), `metadata` (JSON), `zone` (FK `locations.Zone`).
- **`Mission`** — `campaign`, `title`, `description`, `urgency` and `impact` on a 1–3 scale (`THREE_SCALE`), `location` (`locations.Address`), `route` (`locations.Route`), `owner`, `metadata`. Exposes `urgency_label` / `impact_label` and `effective_zone` (its campaign's zone).

  Missions carried a **budget** until the accounting log replaced it: one number and a currency, per mission, summed by the Business Center over linked projects. It went because a budget typed onto a deletable row is an estimate wearing the clothes of a record — the ledger is append-only, missions are not. The constraint that shaped it still holds and will shape whatever wants a currency here next: the field was a `CharField` symbol and **not** an FK to `assets.Asset`, because that model ships in `toto-economy` while this wheel depends on `toto-base` alone, and `check_package_graph.py` forbids the edge.
- **`Sprint`** — `name`, `project`, `start_time`, `end_time`.
- **`Practitioner`** — a professional profile for a `people.Person`, independent of any single project: `role` (`contributor` / `reviewer` / `auditor` / `manager` / `observer`), `is_active`, `work_description`, `metadata`.
- **`ProjectCommitment`** — the join that actually binds a practitioner to a project: `practitioner`, `project`, `hours_per_day`, `is_active`, date range, `metadata`, with a uniqueness constraint on `(practitioner, project)`.
- **`Task`** — `mission`, `column`, optional `sprint` (`SET_NULL`), `title`, `description`, `assignee` and `reviewer` (both FK `Practitioner`, `SET_NULL`), `due_date`, `position`, `weight` on a Fibonacci scale (1/2/3/5/8, `FIB_SCALE`), `metadata`, `completed_at`. Its `clean()` enforces that the chosen column and sprint belong to the same project as the task's `mission → campaign → project`.
- **`DocumentationPage`** — a wiki page. Extends `verbena.AbstractPage` with `project` (the space), a nullable self-FK `parent` (the tree), a nullable `mission`, `order`, `is_manual`, `body_html`, and `vault_file`. `slug` is redeclared to drop the abstract base's global `unique=True` and is scoped by a `(project, slug)` constraint instead — two projects both want a page called "getting-started". `DocumentationSection` is **gone**: a page's prose is one `body_html`, folded out of the section rows by migration `0005` (before the 2026-10-01 reset).

  `body_html` is the read model and every host renders it. `vault_file` points at the `toto.cyprian` document the prose is *written* in, and is **`editable=False` on purpose** — it is the trust anchor `cyprian`'s bridge authorises against, so it must be writable only by `cyprian.bridge.open_document`. Putting it on a form, in admin fields or in a serialiser would let any project member point a page at any document on the instance. See `kanban/plugins/cyprian_bridges.py`.
- **`MissionAttachment`** — a vault file pinned to a mission (`mission`, `vault_file`, `label`): a pointer, never the bytes. The mission page lists only the attachments whose file its viewer may still read — `toto.vault.attach.visible`, asked on every render (2026-10-01): a trashed file is a missing file, and so is one unshared since it was attached; it comes back if the file is restored. Removing an attachment removes the link only. Tests: `tests_trashed_attachments` (under `toto.kanban.testing.settings`).

**Metrics.** `metrics.py` provides `SprintMetricsCalculator` and `MissionMetricsCalculator`, which compute the burndown, velocity, lead-time, per-assignee, and per-campaign figures consumed by both the HTML dashboards and the API.

**Views and API.** `views.py` holds the HTML views (project list/detail, Eisenhower matrix, backlog, mission detail, task create/update/delete, promote/demote, sprint metrics, the wiki). `api_views.py` exposes a JSON API used by mesh clients: read/create endpoints extend `MeshGatedApiView` and task detail/promote/demote extend `CorsApiView` (both from `toto.api.cors`); all require an authenticated user. Project visibility is scoped by `_get_user_projects` (you must be the lead or hold an active `ProjectCommitment`). Creating a task via the API auto-provisions a default campaign and mission if the project has none. Promote/demote move a task to the next/previous column by `position`, returning HTTP 400 at the first/last column. Routes live in `urls.py` under `api/…` (projects, tasks, missions, backlog, sprint-metrics, matrix) alongside the HTML routes.

**Extensibility.** `apps.ready()` autodiscovers `plugins.kanban_plugins`; the `plugins/` package defines mission, mission-tab, and task extension points. The app also ships `forms.py`, `sync_adapters.py`, `templatetags/`, and a `tests_api.py` suite.

**Couplings.** `people` (Person), `locations` (Zone / Address / Route), `verbena` (`AbstractPage`), `vault` (a wiki page's document), `cyprian` (the writer — reached only through `apps.is_installed` guards and a lazily-imported bridge, so a host with the boards and no writer renders its pages read-only), `core.domain` (DomainEntity), `api.cors` (API base views), and `socialhub` (profile links in templates). Economy/tokenisation concerns that once lived here (mission economy, project tokenisation) now live in `toto.mission_economy` and are **not** part of this package.

### memo — presentation editor, player and export

memo has **no database models of its own** (`models.py` documents that the former
`Tag` / `MemoDiagram` / `MemoDeck` / `MemoCard` models were dropped in migration
`0002_drop_memo_models`, before the 2026-10-01 reset). A presentation is one self-contained XML document
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
- **There is no editor.** Decks are authored in the zinnia desktop app and
  arrive here as vault files; this app shows them and nothing else. Nothing in
  memo writes a deck.
- **Detection.** `file_type="pxml"`, in a file named `.pxml`, mapped in the
  vault's `_EXT_MAP` so every ingest door types a deck by its name. The type
  exists because the vault plugin registries are `dict[key -> plugin]` and only
  fire when `key == file_type` — with decks typed `xml` (already claimed by
  `toto.editor`) they could not have a Play button at all. There is **no content
  sniffing**: decks were once typed `presentation` but named `.xml`, so listings
  read up to 300 files off disk and retyped rows as a side effect of rendering a
  page. Vault migration `0021_pxml_file_type` retyped the existing ones (before the
  2026-10-01 reset). The legacy string `presentation` is still read, for the rows
  that migration could not reach (mirrored stubs, s3/remote buckets, encrypted
  decks).
- **URLs** (`urls.py`): `memo:index` (gallery, paginated, real thumbnails),
  `memo:read`, `memo:present`, `memo:export_pdf`. That is all of them.
  The raw-XML source editor is **retired**; its git toolbar moved onto the
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
  git toolbar context).

### Cross-cutting design notes
- **File-as-source-of-truth.** memo, cyprian and sketch store no user content of their own: each keeps everything in the vault file itself and integrates with the vault through its plugin system rather than owning storage.
- **The map beneath the boards.** kanban's `Campaign` and `Mission` have foreign keys to `locations.Zone`, `locations.Address` and `locations.Route`. The map left `toto-base` for `toto-geo` on 2026-10-04, which is why `toto-geo` is a hard dependency of this package since that day. (The `toto-flow` dependency left with antaresia, the last thing here that touched the workflow engine; `toto-flow` still arrives beneath `toto-geo`.)

## Usage

These apps are normally consumed as part of an assembled toto portal, not standalone.

**Install.** Hosts pin the whole suite at one version in `requirements.toto.txt`; `toto-works` is installed at the suite version alongside its siblings — see `VERSION` at the root of the vendored tree, which `scripts/release.py` is the only thing allowed to change. For local/dev work it is installed from the monorepo (editable install of the package under `toto_libs/packages/toto-works`).

**Wire it into a project.** Add the apps you need to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "toto.kanban",
    "toto.memo",
]
```

Include their URLconfs (`toto.kanban.urls`, `toto.memo.urls`) and run migrations (`python manage.py migrate`). kanban needs the map's apps installed as well (`toto.registry.LOCATIONS_APPS`, from `toto-geo`), because its models key into them.

**Import** under the shared namespace, for example:

```python
from toto.kanban.models import Project, Mission, Task
```

**Run the tests** against a host portal, e.g.:

```bash
cd portal && python manage.py test toto.kanban.tests_api
cd portal && python manage.py test toto.memo
```

## Build & packaging

`toto-works` is part of the lockstep-versioned toto suite: all sixteen wheels share a single `VERSION`, kept in `VERSION` at the root of the tree and rewritten only by `scripts/release.py`. It declares its sibling pins in `pyproject.toml`:

- `toto-base` at the suite version
- `toto-geo` at the suite version — kanban's models and its `0001` migration key into `locations` (zones, addresses, routes), which left `toto-base` for `toto-geo` on 2026-10-04

Versions are rewritten only by `scripts/release.py` (never edited by hand), and `scripts/check_package_graph.py` enforces that each wheel owns a disjoint slice of the `toto.*` namespace. The build backend is setuptools with namespace package discovery under `src/`; package data bundles `templates/**/*`, `static/**/*`, and `graph/*.yaml`. Hosts pin the assembled suite in `requirements.toto.txt`.

For the full build and release manual, see the repository root README.
