# toto-flow

**toto workflow engine and compute kernels.** `toto-flow` bundles two tightly-coupled Django apps: **`toto.mandragora`** — interactive Jupyter-style notebooks backed by managed compute kernels — and **`toto.workflows`** — a DAG-based orchestration engine that chains Python "lambda" functions, branching/joining logic and rendered reports. The two ship as one unit because a workflow lambda can bind directly to a mandragora kernel and their database migrations depend on each other. Both are "Studio" apps (the host enables them in `BUILD_STUDIO=1` builds), and the package is one of nine lockstep-versioned wheels that share the `toto.*` PEP 420 namespace.

---

## What it does (functional)

### Notebooks and compute kernels (mandragora)

- **Run code interactively.** Create a notebook, add code or markdown cells, and execute them against a live Python kernel. Standard output, error output and *rich* outputs (matplotlib figures, HTML/image reprs, `execute_result` data) are captured and displayed per cell.
- **Managed kernels with dependencies.** Each notebook gets a named compute kernel with a configurable per-cell timeout, startup timeout, environment variables, and an "auto-close on leave" option. You can declare pip dependencies on a kernel; they are installed into the kernel process when it starts.
- **Two notebook flavours:**
  - **Database notebooks** — cells stored as rows, edited through the notebook UI, with slug-based URLs.
  - **File notebooks (`.tpy`)** — a self-contained XML document stored as an ordinary file in the Vault. The file is the single source of truth (nothing about it lives in the database); it round-trips code, captured console output and rich output exactly, and opens in the notebook editor straight from the Vault file browser.
- **Attach files to a notebook.** Point a notebook at a Vault bucket and its files are injected into every cell as a `vault_files` dict, plus a ready-to-use `files` helper for reading/writing sandboxed workspace files.
- **OCR helper.** The kernel server can extract positioned text from an image (via Tesseract) on request.
- **Promote a cell into a reusable workflow step.** Any code cell can be "promoted" into a workflow `LambdaFunction`, turning notebook experiments into orchestration building blocks.
- **Connectors.** List, validate and execute the shared toto connector types (e.g. HTTP/API and file connectors) from within the notebook surface.

### Workflows and reports (workflows)

- **Design workflows as graphs.** A workflow is a directed acyclic graph of nodes connected by edges, positioned on a canvas. Node kinds are: **lambda** (run a Python function), **split** (branch to one or more outgoing paths), **join** (wait for and merge parallel branches), **report** (render a report document), and **predefined task** (invoke a named, app-registered backend task).
- **Data flows along edges.** Each lambda prints a small JSON result (`{"data": {...}, "route"/"routes": ...}`); downstream nodes receive the merged upstream data, and `route`/`routes` drive which branches of a split fire.
- **Parallelism and gating.** Split/join let you fan out to parallel branches and merge them back; branch keys and default edges let you route conditionally (e.g. approve/reject gates).
- **Validation before you run.** A workflow is checked for empty graphs, missing lambda/report configuration, self-loops, duplicate edges, malformed splits and cycles.
- **Runs are asynchronous and observable.** Triggering a workflow creates a run that executes in the background (via Celery); every node and edge records its own status, inputs, outputs, errors and timing, so a run can be inspected step by step and cancelled.
- **Reports.** A report template holds a small JSON "report definition" describing one visualization — a **card**, **table**, **chart** (bar/line/area/pie) or **text** block. Report nodes turn workflow output into rendered, paged report documents with computed chart geometry, ready to display.
- **Trigger from anywhere.** Other apps can start a workflow by slug with a payload through a one-line Python API.

---

## How it works (technical)

The distribution installs two Django apps under the shared namespace: `toto.mandragora` (app label `mandragora`) and `toto.workflows` (app label `workflows`). The host project adds them to `INSTALLED_APPS` in Studio builds. They are one deployable unit: `workflows.LambdaFunction` has a one-to-one FK into `mandragora.ComputeKernel`, and the `workflows` initial migration declares a dependency on `mandragora`'s initial migration, so the two must migrate together.

### toto.mandragora

**Models** (`mandragora/models.py`):
- `ExecutableUnit` — abstract base: `content`, `stdout`, `stderr`, `created_at`.
- `ComputeKernel` — `name` (unique), `env` (JSON), `timeout_ms` (per-cell, default 5000), `startup_timeout_ms` (default 120000), `auto_close`, `created_at`.
- `KernelDependency` — `kernel` FK, `package_name`, `version_spec`, `install_status` (`pending`/`installing`/`installed`/`failed`), `install_log`, `installed_at`; unique per `(kernel, package_name)`; `pip_specifier()` builds the pip argument.
- `Notebook` — `title`, auto-generated unique `slug`, `kernel` (one-to-one → `ComputeKernel`, `SET_NULL`), `bucket` (FK → `vault.Bucket`, nullable; its files are injected into cells).
- `Cell` — extends `ExecutableUnit`: `notebook` FK, `cell_type` (`code`/`markdown`), `position` (ordering), `execution_count`, `rich_output` (JSON list of Jupyter output objects).

**Kernel execution.** Code does **not** run inside the Django process. A `KernelClient` (`mandragora/kernel.py`) speaks ZeroMQ REQ-REP to a standalone **`KernelServer`** process (`mandragora/kernel_server.py`), addressed by `settings.KERNEL_SERVER_ADDR` (default `tcp://127.0.0.1:5555`). The server keeps one `NotebookKernel` per notebook id, each wrapping a real Jupyter kernel via `jupyter_client.KernelManager` (requires `ipykernel`). Supported actions are `start`, `stop`, `execute`, `status`, `kill` and `ocr`. On `start` the server pip-installs the kernel's declared dependencies, injects a `files` (`toto.core.file_client.FileClient`) helper, and, when a bucket is attached, a `vault_files` name→path map. `execute` drains the kernel's iopub channel and returns `stdout`, `stderr`, `execution_count` and a `rich_output` list.

**Cell run flow.** `run_cell` (HTTP) enqueues the Celery task `execute_cell_task`, which calls `client.execute(notebook_id, cell.content)` and persists the returned streams/rich output onto the `Cell`; the UI polls `cell_result`. Cell execution requires a live Celery worker (`toto.celery_utils.celery_available`). Kernel lifecycle endpoints (`start_kernel`, `stop_kernel`, `check_kernel`, `kernel_dependencies`, `notebook_vault_files`) proxy to the same client.

**`.tpy` file notebooks.** `mandragora/tpy_format.py` defines a versioned XML format (`TpyNotebook`/`TpyCell`/`TpyDependency`) that stores the dependency list plus each cell's source, captured stdout/stderr, execution count and rich output. Values with XML-illegal control characters are transparently base64-encoded (`enc="base64"`) so the document stays well-formed and round-trips exactly; rich output is stored as ASCII-safe JSON. These notebooks are plain `.xml` Vault files (`is_notebook()` sniffs the `<notebook>` root); `mandragora/plugins/vault_editor_plugins.py` registers a `VaultEditorPlugin` (key `"notebook"`) so they open in the editor at the `tpy/<file_pk>/…` routes, with their own start/stop/status/run kernel endpoints.

**Connectors** (`mandragora/connectors.py`) are thin re-exports of `toto.core.connectors` (`list`/`execute`/`validate`). **Promotion** (`promote_cell_to_lambda`) upserts a cell's code into a `workflows.LambdaFunction`, the main bridge from mandragora into the workflow engine.

**Management commands:** `run_kernel_server` (boots the ZMQ `KernelServer`, `--bind-addr` default `tcp://0.0.0.0:5555`), `ingress_mandragora` (seeds a default "Python 3" kernel, a starter notebook, sample connectors, and — only under `FULL_INGRESS` — demo workflows/runs/reports), and `example_workflow`.

### toto.workflows

**Models** (`workflows/models.py`):
- `LambdaFunction` — `function_name` (unique), `content` (Python source), `stdout`/`stderr`, `kernel` (one-to-one → `mandragora.ComputeKernel`).
- `Workflow` — `name`, auto-slug, `description`.
- `WorkflowNode` — `node_type` (`lambda`/`split`/`join`/`report`/`predefined_task`), `label`, `task_name`, optional `lambda_function` FK, optional `report_template` FK, `config` (JSON), `position_x`/`position_y`.
- `WorkflowEdge` — `source`/`target` FKs to nodes, `branch_key`, `is_default`; unique per `(source, target)`.
- `WorkflowRun` — `status` (`pending`/`running`/`completed`/`failed`), `input_data`/`output_data`, `started_at`/`completed_at`/`created_at`.
- `WorkflowNodeRun` — `workflow_run` + `node` FKs (unique together), `status` (adds `skipped`), `input_data`/`output_data`, `error`, `celery_task_id`, timings.
- `WorkflowEdgeRun` — `workflow_run` + `edge` FKs (unique together), `activated`, `activated_at`.
- `ReportTemplate` — `name`, slug, `report_type`, `definition` (JSON, validated), timestamps. `Report` — snapshot `definition` + `data` + `metadata`, links back to `template`, `workflow_run` and `source_node_run`, `status` (`draft`/`published`/`archived`). `ReportPage` — `report` FK, `key` (unique per report), `title`, `order`, `blocks`/`data` JSON.

**Execution engine** (`workflows/services/executor.py`). `WorkflowExecutor` runs the DAG: it starts nodes with no incoming edges, then advances by inspecting completed node runs and activated edge runs (a Kahn-style topological walk), merging upstream `data` dicts and `routes` lists into each node's input. Per node type:
- **lambda** — prepends the input as `_input` and executes `LambdaFunction.content`. By default it runs in-process (`exec` with `_input` and a `files` FileClient in scope); if `settings.WORKFLOW_KERNEL_CLIENT` is set, it runs through that client instead. The last JSON line of stdout is parsed and normalized (`workflows/output.py`) into `{"data": ..., "routes": [...]}`.
- **split** — passes input through; `_activate_outgoing_edges` fires outgoing edges whose `branch_key` is in the emitted `routes`, falling back to the single `is_default` edge.
- **join** — waits until every activated upstream edge has a completed source node run, then merges their data/routes.
- **report** — delegates to `services/reports.py` to create a `Report` from the node's `report_template`.
- **predefined_task** — looks the `task_name` up in the registry; a Celery-registered task is dispatched asynchronously (`current_app.send_task`), otherwise the callable runs synchronously.

In `async_lambdas` mode each lambda node is queued as its own Celery task (`execute_lambda_node_task`) with a soft time limit derived from the kernel's `timeout_ms` (or `WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS`, default 30s). Completion is detected when no node run is left pending/running.

**Validation** (`services/validator.py`): non-empty graph; lambda nodes require a `lambda_function`; report nodes require a `report_template`; no self-loops or duplicate edges; split nodes need exactly one incoming edge, at least one outgoing edge and at most one default; and the graph must be acyclic (Kahn's algorithm).

**Predefined-task registry** (`predefined_tasks.py`): `register` / `register_celery` / `get_celery_task` / `run` / `available`. `WorkflowsConfig.ready()` mirrors Django's admin autodiscover, importing every installed app's `predefined_tasks` module so tasks register regardless of which `BUILD_*` flags or process (web, Celery, management command) is active.

**Built-in connectors** (`services/connectors.py`): `file_read` and `file_write` `BaseConnector`s registered with `toto.core.connectors`, sandboxed under `settings.WORKFLOW_FILE_CONNECTOR_ROOT` (default `MEDIA_ROOT/workflow-files`) with a path-escape guard and a `WORKFLOW_FILE_CONNECTOR_MAX_BYTES` read cap.

**Report rendering** (`services/reports.py`): a report definition (validated by `validate_report_definition` to exactly one page and one block) is expanded into `ReportPage`s; `render_report`/`resolve_block` resolve dotted data paths and precompute geometry for cards, tables and charts (bar heights, line point strings, pie `conic-gradient`, y-ticks).

**HTTP surface** (`workflows/urls.py`, `views.py`): UI views for the workflow list, workflow detail (graph editor) and run detail; a JSON API for report templates, reports, workflow CRUD, node/edge CRUD, `validate`, and run list/detail/cancel. Triggering a run validates the workflow, checks Celery availability and dispatches `start_workflow_run_task`. The programmatic entry point is `toto.workflows.api.trigger_workflow(slug, payload)`, which creates a `WorkflowRun`, queues it and returns immediately.

> Note: `services/human_task.py` and `services/triggers.py` are present but currently empty placeholders; human-approval style flows are modelled today with split/branch-key routing rather than a dedicated mechanism.

### Cross-app coupling and shared dependencies

- **mandragora ↔ workflows:** `workflows.LambdaFunction.kernel` → `mandragora.ComputeKernel` (one-to-one); cell promotion writes `LambdaFunction`s; the `workflows` migration depends on `mandragora`. A lambda may execute against a mandragora kernel via `WORKFLOW_KERNEL_CLIENT`.
- **On the sibling wheel `toto-base`** (the only declared dependency): `toto.core` (connectors, `FileClient`), `toto.ingress` (`IngressCommand`), `toto.vault` (`Bucket`, `VaultFile`, `VaultEditorPlugin`), `toto.ui` (`PageProcessor`), `toto.celery_utils`, and `toto.api` (seed connectors).
- **Third-party:** Django, Django REST framework, Celery, `pyzmq`, `jupyter_client`/`ipykernel`, and optionally `pytesseract`/Pillow for OCR.

### Configuration knobs

`KERNEL_SERVER_ADDR`, `WORKFLOW_KERNEL_CLIENT`, `WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS`, `WORKFLOW_FILE_CONNECTOR_ROOT`, `WORKFLOW_FILE_CONNECTOR_MAX_BYTES`.

---

## Usage

`toto-flow` is a library of Django apps consumed by a host toto project, not a standalone service.

**Install** (normally pinned in the host's `requirements.toto.txt` alongside its siblings):

```bash
pip install toto-flow
```

**Enable** the apps in the host's Django settings (Studio builds set `BUILD_STUDIO=1`), then apply migrations:

```python
INSTALLED_APPS = [
    # ...
    "toto.mandragora",
    "toto.workflows",
]
```

```bash
python manage.py migrate
```

**Run the kernel server** (separate process; required for notebook/kernel execution) and a Celery worker (required for cell and workflow runs):

```bash
python manage.py run_kernel_server --bind-addr tcp://0.0.0.0:5555
```

**Seed demo data** (optional; set `FULL_INGRESS=1` for the example workflows, runs and reports):

```bash
python manage.py ingress_mandragora
python manage.py ingress_workflows   # readiness check
```

**Trigger a workflow from code:**

```python
from toto.workflows.api import trigger_workflow

run = trigger_workflow("data-pipeline", {"data": {"item_id": 1}})
# run is a WorkflowRun; execution proceeds asynchronously via Celery.
# Poll run.status or the run-detail API to track completion.
```

**Develop against it:** editable install (`pip install -e .` from `packages/toto-flow`), then run the app suites, e.g. `python manage.py test toto.workflows toto.mandragora` (tests live in `workflows/tests.py`, `mandragora/tests.py`, `tests_gating.py`, `tests_tpy.py`). Note the disjoint-namespace rule: this wheel owns only the code under its own `src/toto/` — keep new modules within `toto.mandragora`/`toto.workflows`.

---

## Build & packaging

`toto-flow` is one wheel in the lockstep **toto** suite (nine distributions sharing the `toto.*` PEP 420 namespace). It uses a `src/` layout built with `setuptools` (`build_meta`); `[tool.setuptools.packages.find]` gathers `toto*` with `namespaces = true`, and package data ships `templates/**/*`, `static/**/*` and `graph/*.yaml`.

- **Version:** currently `1.6`. All suite versions move together and are rewritten only by the repository's release script — never edit them by hand.
- **Sibling pin:** depends on `toto-base==1.6`. Hosts pin `toto-flow` (and its siblings) in `requirements.toto.txt`.
- **Partitioning:** the repo's package-graph check enforces that each wheel owns a disjoint slice of the namespace.

Build and release are driven by the repository's scripts; see the **repository root README** for the full build, versioning and release manual.
