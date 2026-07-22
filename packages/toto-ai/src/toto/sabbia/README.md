# sabbia

Headless agentic/chatbot **backend**. Hosts any number of chat agents; each agent
only chats with a user over websockets. No UI — the floating chat widget lives in
the separate `toto.steven` app.

## Pieces

- `models.py` — `Agent`, `AgentConnector` (encrypted creds via Gervazy), `Conversation`,
  `ChatMessage`, `PlatformChatbot` (links a `core.Platform` to the agent its widget shows).
- `endpoints/` — pluggable chat transports. Each implements `ChatEndpoint.chat(messages) -> str`.
  Add a type = new class + one line in `endpoints/__init__.py:REGISTRY`.
  - `openai` — OpenAI Chat Completions; key decrypted from the sabbia vault.
  - `ollama` — calls the `toto.vicuna` chat endpoint (which proxies to local Ollama).
- `consumers.py` / `routing.py` — `ws/sabbia/agent/<slug>/`, single-shot replies, logged-in only.
- `vault.py` + `sabbia_init_vault` — server-side Gervazy system strongbox unlocked by
  `SABBIA_VAULT_PASSWORD` (mirrors `toto.sso_master`'s signing vault).
- `ingress_sabbia` — seeds the Steven (OpenAI) agent + optional Ollama agent + PlatformChatbot.

## Deployment flags

- `BUILD_SABBIA` — install this backend (implies `BUILD_STUDIO` for websockets).
- `SABBIA_OPENAI` — enable the OpenAI endpoint + seed Steven (needs `OPENAI_API_KEY` to seed the key,
  `SABBIA_VAULT_PASSWORD` at runtime).
- `SABBIA_OLLAMA` — install `toto.vicuna` + enable the Ollama endpoint + seed an Ollama agent.

## Setup

```
SABBIA_VAULT_PASSWORD=... python manage.py sabbia_init_vault
OPENAI_API_KEY=sk-... python manage.py ingress_sabbia
```
