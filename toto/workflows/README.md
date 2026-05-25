# toto.workflows

*(Studio only — requires BUILD_STUDIO=1)*

DAG-based workflow orchestration engine. Workflows define directed graphs of nodes; runs execute nodes in topological order, with edges carrying data between them. Used by `texlab`, `weather`, and `steven`.

## Models

- `LambdaFunction` — a Python snippet that can be called as a workflow node. Fields: `name`, `slug`, `code` (Python source), `signature` (JSON schema for inputs/outputs), `is_active`.

- `Workflow` — a named DAG. Fields: `name`, `slug`, `community` (FK, nullable), `is_active`, `metadata`.

- `ReportTemplate` — a Jinja/HTML template for workflow report output. Fields: `name`, `slug`, `template_source`, `is_active`.

- `WorkflowNode` — a node in the workflow DAG. Fields: `workflow` FK, `name`, `node_type` (`lambda / http_request / condition / merge / split / kernel_cell / compile_latex / weather_fetch / agent_call`), `config` (JSON — node-specific parameters), `position_x` / `position_y`.

- `WorkflowEdge` — a directed connection between two nodes. Fields: `workflow`, `source` / `target` (FKs to `WorkflowNode`), `condition` (JSON — optional expression that must evaluate truthy), `data_mapping` (JSON — how to map source outputs to target inputs).

- `WorkflowRun` — one execution of a workflow. Fields: `workflow`, `status` (`pending / running / success / failed / cancelled`), `triggered_by` (FK to `people.Person`, nullable), `started_at`, `finished_at`, `input_data` / `output_data` (JSON), `error_message`.

- `WorkflowNodeRun` — execution of one node within a run. Fields: `run` FK, `node` FK, `status`, `input_data` / `output_data` (JSON), `started_at`, `finished_at`, `error_message`, `retries`.

- `WorkflowEdgeRun` — data flowing across one edge in a run. Fields: `run`, `edge`, `data` (JSON), `transferred_at`.

- `Report` — a rendered output document produced by a workflow run. Fields: `run` FK, `template` FK, `title`, `rendered_html`, `vault_file` (FK to `vault.VaultFile`, nullable — PDF export), `created_at`.

- `ReportPage` — a page within a multi-page report. Fields: `report` FK, `page_number`, `content` (HTML).

## Key coupling

- `texlab.CompileRun.workflow_run` — LaTeX compiles can be workflow nodes.
- `weather.WeatherObservation.workflow_run` / `ForecastSession.workflow_run` — weather fetches are workflow nodes.
- `mandragora.Cell` — kernel cell execution is a workflow node type.
- `steven.AgentRun` — agent calls are a workflow node type.

## Dependencies

- `mandragora` — ComputeKernel FK — workflow nodes can execute in a mandragora kernel
