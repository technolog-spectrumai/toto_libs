# toto.steven

*(Studio only — requires BUILD_STUDIO=1)*

AI agent management. Defines named agent profiles with LLM configuration and tool sets. Runs are tracked per-agent with full input/output and token usage.

## Models

- `AgentConnector` — extends `api.ApiConnector`. LLM-specific outbound connector. Adds: `model_name` (e.g. `claude-sonnet-4-6`), `system_prompt`, `temperature`, `max_tokens`, `provider` (`anthropic / openai / mistral / custom`).

- `AgentProfile` — a named autonomous agent. Fields: `name`, `slug`, `user` (OneToOne FK to `auth.User` — the agent's identity for actions it takes), `connector` (FK to `AgentConnector`), `description`, `is_active`, `community` (FK, nullable), `metadata`.

- `AgentTool` — a tool available to an agent. Fields: `agent` (FK to `AgentProfile`), `tool_type` (`web_search / code_exec / file_read / api_call / workflow_trigger`), `config` (JSON), `is_enabled`.

- `Conversation` — a thread of messages for a user's session with an agent. Fields: `agent` FK, `user` (FK to `auth.User`), `title`, `started_at`, `last_message_at`, `is_archived`.

- `ChatMessage` — one message in a conversation. Fields: `conversation` FK, `role` (`user / assistant / system / tool`), `content` (text), `tool_calls` (JSON), `tool_results` (JSON), `created_at`, `tokens_used`.

- `AgentRun` — a single programmatic agent invocation (outside a conversation). Fields: `agent` FK, `triggered_by` (FK to `people.Person`, nullable), `workflow_run` (FK to `workflows.WorkflowRun`, nullable), `status` (`pending / running / success / failed`), `input_data` (JSON), `output_data` (JSON), `error_message`, `tokens_input`, `tokens_output`, `started_at`, `finished_at`.

## Key coupling

- `api.ApiConnector` — `AgentConnector` inherits all connector fields; secrets live in `gervazy`.
- `workflows.WorkflowRun` — agents can be invoked as workflow nodes (`agent_call`).

## Dependencies

- `api` — AgentConnector subclasses ApiConnector for LLM provider config
