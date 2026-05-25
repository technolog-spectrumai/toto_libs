# toto.mandragora

*(Studio only — requires BUILD_STUDIO=1)*

Jupyter-style compute kernels over WebSockets. Notebooks contain cells; execution is forwarded to a ZMQ kernel server process and results are streamed back.

## Models

- `ExecutableUnit` — abstract base for anything that can be executed. Fields: `source` (code text), `status` (`idle / queued / running / done / error`), `execution_count`, `last_run_at`, `output` (JSON — list of output objects per Jupyter msg spec).

- `ComputeKernel` — a named kernel session. Fields: `name`, `kernel_id` (UUID, the ZMQ kernel process ID), `language` (`python / r / julia`), `status` (`starting / idle / busy / dead`), `owner` (FK to `people.Person`), `created_at`, `last_activity_at`.

- `KernelDependency` — a Python package or system dependency required by a kernel. Fields: `kernel` FK, `package_name`, `version_spec` (e.g. `>=1.2`), `install_status` (`pending / installed / failed`).

- `Notebook` — a named collection of cells. Fields: `name`, `slug`, `kernel` (FK to `ComputeKernel`), `owner` (FK), `community` (FK, nullable), `is_public`, `created_at`.

- `Cell` — extends `ExecutableUnit`. One notebook cell. Fields: `notebook` FK, `cell_type` (`code / markdown / raw`), `order`.

## How it works

1. Client sends `execute` over WebSocket to the Channels consumer.
2. Consumer forwards the request to the kernel server at `KERNEL_SERVER_ADDR` (`tcp://kernel_server:5555`) via ZMQ.
3. The kernel server (standalone Python process) runs the code in a managed subprocess and streams outputs back.
4. Consumer relays outputs to the client and persists them on `ExecutableUnit.output`.

## Key coupling

- `KERNEL_SERVER_ADDR` — must point to the running ZMQ kernel server.
- `workflows.WorkflowNode` — workflow nodes of type `kernel_cell` can execute a `Cell`.
