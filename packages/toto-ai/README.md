# toto-ai

**toto AI agents, embeddings and assistants.** This distribution bundles the AI layer of the toto
suite: a headless chat-agent backend (**sabbia**), the floating "Ask AI" chat widget that end users
see (**steven**), and the local-model runtime that talks to Ollama and produces embeddings
(**vicuna**). Together they let a toto deployment offer an on-page AI assistant backed by either
cloud OpenAI or a private, local model, optionally grounded in the site's own knowledge graph. All
three apps live under the shared `toto.*` PEP 420 namespace and are installed as Django apps.

---

## What it does (functional)

From an operator's point of view, toto-ai adds a site AI assistant and the plumbing behind it:

- **An "Ask AI" chat window on every page.** A hovering widget appears site-wide for logged-in
  users. They click it, type, and chat with your assistant in real time over a websocket. It shows
  nothing for anonymous visitors or when no assistant has been configured.
- **Configurable assistants ("agents").** Each agent has its own name, avatar, description,
  personality (system prompt), model, and "temperature" (how creative vs. focused it is). You create
  and edit them in the Django admin.
- **Cloud or fully-local brains.** An agent can be backed by **OpenAI (ChatGPT)** in the cloud, or by
  a **local Ollama model** running on your own hardware so no conversation data leaves your server.
- **Answers grounded in your own data (optional).** An agent can be switched into a retrieval mode
  that pulls context from the site's knowledge graph before answering, so replies are based on your
  content rather than the model's general knowledge. If the graph is unavailable, the agent silently
  falls back to a normal chat reply — chat never breaks.
- **Safe credential storage.** API keys (e.g. your OpenAI key) are stored encrypted at rest, unlocked
  automatically by the server. Operators never paste keys into page config.
- **Per-site assistant selection.** Each platform/site can be pointed at a specific agent, so
  different sites can present different assistants (or none).
- **Conversation history.** Every chat is saved and browsable in the admin for review.
- **Local model management.** Register your Ollama servers, see which models are pulled and how large
  they are, pull/refresh models, test reachability, and detect whether a GPU is present — all from
  the admin plus a few management commands.
- **Text embeddings.** A single embedding service turns text into vectors for search and
  graph/retrieval features, using either local Ollama or OpenAI.

Turning features on is driven by deployment flags (`BUILD_STEVEN` / `BUILD_SABBIA`, `SABBIA_OPENAI`,
`SABBIA_OLLAMA`) so a host only installs the pieces it needs.

---

## How it works (technical)

toto-ai ships three Django apps under `toto.*`. **steven** is the front-end shell, **sabbia** is the
agent backend, and **vicuna** is the local-model/embedding runtime.

### `toto.sabbia` — headless agent backend

The core. Hosts any number of chat agents; each agent chats 1:1 with a user over a websocket. It has
no UI of its own.

- **Models** (`models.py`):
  - `Agent` — a chat-only agent: `name`/`slug`, `system_prompt`, `endpoint_type`, `model_name`,
    `temperature`, `is_active`, a non-secret `endpoint_config` JSON blob, and a nullable FK to a
    `connector`. `endpoint_type` choices come dynamically from the endpoint registry.
  - `AgentConnector` — a concrete subclass of `toto.api.ApiConnector`, so agent API keys live
    encrypted in Gervazy and are read via `decrypt_api_secret(vault_session=...)`.
  - `PlatformChatbot` — one-to-one link from a `core.Platform` to the `Agent` its widget shows, with
    an `is_enabled` flag.
  - `Conversation` / `ChatMessage` — persisted chat history (roles `user` / `assistant`).
- **Pluggable chat endpoints** (`endpoints/`): the strategy pattern. Each transport subclasses
  `ChatEndpoint` and implements `chat(messages) -> str` (synchronous, blocking HTTP). A `REGISTRY`
  in `endpoints/__init__.py` maps `type_key -> class` and auto-derives both `ENDPOINT_CHOICES` and
  the `Agent.endpoint_type` field choices, so adding a transport is one class + one registry line.
  - `openai` (`OpenAIChatEndpoint`) — OpenAI Chat Completions; API key decrypted from the sabbia
    vault. Defaults to `gpt-4.1-mini`.
  - `ollama` (`OllamaChatEndpoint`) — does **not** call Ollama directly; it POSTs to the internal
    `toto.vicuna` chat view (`reverse("vicuna:chat")` under `SABBIA_INTERNAL_BASE_URL`), passing
    `VICUNA_INTERNAL_TOKEN`. Keeps all Ollama access funneled through vicuna.
- **Websocket transport** (`consumers.py`, `routing.py`): `AgentChatConsumer` (Django Channels)
  serves `ws/sabbia/agent/<slug>/`. It rejects unauthenticated sockets (close code 4401), loads the
  active agent (4404), builds the endpoint strategy, creates a `Conversation`, and then does
  single-shot replies: each `user_message` is saved, the full history (`system_prompt` + turns) is
  sent to `endpoint.chat` via `sync_to_async`, and the assistant reply is saved and returned. The
  wire protocol uses `ack` / `typing` / `assistant_message` / `error` frames; content is capped at
  `MAX_CONTENT_CHARS` (8000).
- **GraphRAG path** (`graphrag_llm.py`, consumer `_maybe_graphrag_reply`): when an agent's
  `endpoint_config["rag"]["enabled"]` is set, the consumer builds a `neo4j-graphrag` LLM from the
  agent (`build_llm`) and calls `toto.ravioli.rag.run_graphrag` (from the graph layer) with `top_k`
  and `text2cypher` knobs. This is lazy-imported and **fails soft**: any error (ravioli absent,
  graph down, retrieval/LLM error) logs a warning and returns `None`, so the consumer falls back to
  the plain endpoint. This is a deliberate one-way coupling — see the design note below.
- **Credential vault** (`vault.py`): a single server-side Gervazy "system strongbox"
  (`sabbia-system`, owned by the `sabbia-vault` service account) holds every agent secret. It is
  unlocked with `SABBIA_VAULT_PASSWORD` (Argon2id → UKEK → VMK → DEK → AES-GCM), with the unlocked
  session cached per process so the KDF runs once. It mirrors `toto.sso_master`'s signing vault and
  lets the request-less websocket consumer resolve credentials. Helpers cover store / re-encrypt
  (per-secret key rotation) / retire and best-effort audit logging (never logs plaintext).
- **Admin** (`admin.py`): `Agent`, `AgentConnector` (with the encrypted-secret form mixin from
  `toto.api`), `PlatformChatbot`, and `Conversation` (with inline messages).
- **Management commands**: `sabbia_init_vault` (create the strongbox, idempotent) and
  `ingress_sabbia` (seed the Steven/OpenAI agent when `SABBIA_OPENAI`, an Ollama agent when
  `SABBIA_OLLAMA`, store the OpenAI key in the vault, and point the active platform's
  `PlatformChatbot` at Steven — or the Ollama agent if OpenAI is off). RAG is seeded enabled only
  when `RAVIOLI_ENABLED`.

### `toto.steven` — the chat-widget UI

A thin shell — no models, urls, or views. Its `AppConfig.ready()` autodiscovers floating plugins.
`plugins/floating_plugins.py` registers `StevenChatPlugin`, a `toto.core.FloatingPlugin` (key
`steven_chat`) rendered site-wide by `oya/base.html`'s `{% render_floating_plugins %}` via
`templates/steven/plugins/floating_chat.html`. The widget:

- is **visible only to authenticated users** (chat needs an authenticated socket);
- resolves the agent to talk to by looking up the first `Platform`'s enabled, active
  `PlatformChatbot`, degrading to rendering nothing when sabbia isn't installed (import guard) or no
  chatbot is configured;
- opens `ws/sabbia/agent/<slug>/` against the sabbia backend.

### `toto.vicuna` — Ollama runtime & embedding service

Owns all local-model access and embedding generation.

- **Models** (`models.py`): `OllamaServer` (a deployment: `host`, `uses_gpu`, `is_active`,
  `last_synced`) and `OllamaModel` (a model pulled on a server, unique per `(server, name)`, with
  reported `size_bytes`).
- **Internal chat view** (`views.py`, `urls.py`): a CSRF-exempt POST `vicuna:chat` (`chat/`) that
  proxies to the local Ollama `/api/chat`. It is **server-to-server only** (consumed by sabbia's
  Ollama endpoint), optionally guarded by `VICUNA_INTERNAL_TOKEN` via the `X-Internal-Token` header,
  and clamps the requested model to the allow-list before calling Ollama.
- **Chat model resolution** (`chat.py`): the single place that decides which Ollama chat model to
  use. Nothing else hardcodes a model string. It reads `VICUNA_CHAT_MODEL` (default `qwen3:1.7b`)
  against an allow-list `VICUNA_CHAT_MODEL_CHOICES` (default `qwen3:0.6b` / `qwen3:1.7b` /
  `qwen3:4b`), and `installed_ollama_chat_models()` prefers synced `OllamaModel` rows, falling back
  to a live `/api/tags` query and finally to the full allow-list so the admin never breaks.
- **Embedding service** (`embeddings.py`): the sole owner of embedding calls (ravioli, sabbia, etc.
  must go through it, never call Ollama/OpenAI directly). A strategy pattern selects the provider via
  `VICUNA_EMBEDDING_PROVIDER` — `ollama` (default, local `qwen3-embedding:0.6b`) or `openai`
  (`text-embedding-3-small`, 1536-dim). Public API: `embed_texts`, `embed_text`,
  `embedding_dimension`, gated by `VICUNA_EMBEDDINGS_ENABLED`; failures raise `EmbeddingUnavailable`.
  Note: vector dimension depends on the active provider, so switching providers requires re-indexing
  (`ravioli_reindex_embeddings`).
- **Runtime detection** (`runtime.py`): `detect_gpu()` probes for an NVIDIA GPU without importing
  torch/CUDA (checks `nvidia-smi`, `/dev/nvidia0`, `NVIDIA_VISIBLE_DEVICES`) and never raises.
- **Admin** (`admin.py`): manage `OllamaServer`/`OllamaModel` with a reachability "test server"
  button and actions to sync the model list (`/api/tags`) or pull-then-sync the allowed models.
- **Management commands**: `ingress_vicuna` (create the default `OllamaServer` and sync),
  `vicuna_pull_chat` (pull chat model(s), GPU check, smoke test — honours `VICUNA_REQUIRE_GPU`), and
  `vicuna_pull_embeddings` (pull the embedding model; no Neo4j required).

### Key couplings and design decisions

- **Hard dependency:** only `toto-base==1.6` (via `pyproject.toml`). Everything sabbia/steven/vicuna
  reach for at runtime — `toto.api` (connectors), `toto.gervazy` (crypto vault), `toto.core`
  (`Platform`, plugins, `plugin_autodiscover`), `toto.ingress`, `toto.conf` — lives in toto-base.
- **Graph integration is one-way and lazy.** toto-ai never imports the graph package at module load;
  `toto.ravioli.rag` / `neo4j-graphrag` are imported inside functions and fail soft. This is
  intentional: **`toto-graph` depends on `toto-ai`, never the reverse** (noted in `pyproject.toml`),
  keeping the dependency graph acyclic. `check_package_graph.py` enforces the partition.
- **Single chokepoints.** All Ollama chat model choices flow through `vicuna/chat.py`; all embedding
  calls through `vicuna/embeddings.py`; all Ollama access from sabbia goes through the vicuna chat
  view rather than talking to Ollama directly.
- **Separation of UI and backend.** steven renders nothing on its own logic beyond visibility/agent
  lookup; all model calls, credentials, and history live in sabbia. steven degrades gracefully when
  sabbia is absent.
- **Third-party runtime libs are optional** and lazy-imported: `openai`, `ollama`, `neo4j-graphrag`,
  and Django Channels (websockets). Missing ones surface as clear errors only on the path that needs
  them.

---

## Usage

### Install

toto-ai is normally installed as part of a toto deployment, pinned in the host's
`requirements.toto.txt` alongside its siblings and resolved from the pre-built wheels (see
**Build & packaging**). It is a Django app bundle; enable the apps you need in `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "toto.sabbia",   # agent backend (needs Channels/websockets)
    "toto.steven",   # floating chat widget UI
    "toto.vicuna",   # local Ollama runtime + embeddings (needed for local agents)
]
```

In practice, installation and app wiring are driven by deployment flags: `BUILD_STEVEN` installs the
widget and implies `BUILD_SABBIA`; `BUILD_SABBIA` installs the backend and implies the websocket
(`BUILD_STUDIO`) stack; `SABBIA_OPENAI` enables the OpenAI endpoint and seeds Steven; `SABBIA_OLLAMA`
installs vicuna and enables the local Ollama endpoint.

### Configure & seed

```bash
# 1. Create the encrypted credential vault (once per deployment; idempotent).
SABBIA_VAULT_PASSWORD=... python manage.py sabbia_init_vault

# 2. Seed agents + link the platform's chatbot.
#    Stores OPENAI_API_KEY in the vault when SABBIA_OPENAI is on.
OPENAI_API_KEY=sk-... python manage.py ingress_sabbia

# 3. (Local models) register the default Ollama server and pull models.
python manage.py ingress_vicuna
python manage.py vicuna_pull_chat            # or --all / --model qwen3:4b
python manage.py vicuna_pull_embeddings      # only if using local embeddings
```

Relevant settings:

- **sabbia:** `SABBIA_VAULT_PASSWORD` (runtime unlock — keep secret; losing it makes stored secrets
  unreadable), `OPENAI_API_KEY` (seed only), `SABBIA_INTERNAL_BASE_URL` (default
  `http://127.0.0.1:8000`), `RAVIOLI_ENABLED` (seed RAG on/off).
- **vicuna:** `VICUNA_OLLAMA_HOST` (default `http://localhost:11434`), `VICUNA_INTERNAL_TOKEN`,
  `VICUNA_CHAT_MODEL` / `VICUNA_CHAT_MODEL_CHOICES` / `VICUNA_CHAT_TEMPERATURE` /
  `VICUNA_CHAT_TIMEOUT`, `VICUNA_REQUIRE_GPU`, and embeddings: `VICUNA_EMBEDDINGS_ENABLED`,
  `VICUNA_EMBEDDING_PROVIDER`, `VICUNA_EMBEDDING_MODEL`, `VICUNA_OPENAI_EMBEDDING_MODEL`,
  `VICUNA_EMBEDDING_TIMEOUT`.

### Import / develop against it

- Generate embeddings from any app: `from toto.vicuna.embeddings import embed_text, embed_texts`
  (raises `EmbeddingUnavailable` when disabled/misconfigured). Never call Ollama/OpenAI directly.
- Resolve the local chat model: `from toto.vicuna.chat import resolve_ollama_chat_model,
  default_ollama_chat_model`.
- **Add a new chat transport:** subclass `toto.sabbia.endpoints.base.ChatEndpoint`, set `type_key`
  and `label`, implement `chat(messages) -> str`, and add one line to
  `endpoints/__init__.py:REGISTRY`; the model's `endpoint_type` choices update automatically.
- Day-to-day operation (create agents, store keys, pick the per-platform agent, browse
  conversations, sync/pull Ollama models) is done through the Django admin.

---

## Build & packaging

toto-ai is one of the nine lockstep-versioned wheels in the toto suite (siblings: `toto-base`,
`toto-chat`, `toto-flow`, `toto-geo`, `toto-graph`, `toto-media`, `toto-ops`, `toto-works`). Every
package shares a single version from the repo's `VERSION` file (currently **1.6**) and pins its
siblings exactly. toto-ai declares just one sibling dependency: **`toto-base==1.6`**; its graph
integration stays a lazy, one-directional coupling so `toto-graph` can depend on toto-ai and not the
reverse.

- Versions are rewritten only by `scripts/release.py` (never edit `version` or `toto-*==` pins by
  hand); `scripts/check_package_graph.py` and the versioning tests enforce coherence and the disjoint
  `src/toto/*` partition.
- Wheels are built with `scripts/build_wheels.py` (offline, `--no-index` install against the built
  files).
- Hosts pin the resulting wheels in their `requirements.toto.txt`.

For the full build/versioning/release manual, see the repo root README.
