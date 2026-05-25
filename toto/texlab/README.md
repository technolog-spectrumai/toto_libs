# toto.texlab

*(Studio only — requires BUILD_STUDIO=1)*

LaTeX compilation service over WebSockets. Workspaces hold `.tex` and supporting files; compile runs invoke a LaTeX engine and return PDF or image output.

## Models

- `LatexWorkspace` — a named LaTeX project. Fields: `name`, `slug`, `bucket` (FK to `vault.Bucket` — file storage for this workspace), `owner` (FK to `people.Person`), `is_public`, `created_at`.

- `LatexFile` — a file within a workspace. Fields: `workspace` FK, `filename`, `vault_file` (FK to `vault.VaultFile`), `is_main` (the entrypoint `.tex` file), `updated_at`.

- `CompileRun` — a single compilation attempt. Fields: `workspace` FK, `latex_file` (FK to the main file), `status` (`queued / running / success / failed`), `compiler` (`pdflatex / xelatex / lualatex`), `output_pdf` (FK to `vault.VaultFile`, nullable), `log_output` (text), `duration_ms`, `workflow_run` (FK to `workflows.WorkflowRun`, nullable — for scheduled compiles), `created_at`.

## How it works

- Compile requests are submitted via WebSocket or HTTP.
- The Channels consumer queues a `CompileRun`, streams log output back to the client in real time, and stores the output PDF in vault on success.

## Key coupling

- `vault.VaultFile` / `vault.Bucket` — all files stored via vault.
- `workflows.WorkflowRun` — compile runs can be triggered by the workflow engine.
