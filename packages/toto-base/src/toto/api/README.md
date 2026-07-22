# toto.api

Outbound API connector registry and email delivery. Stores configuration for external API integrations; credentials are never stored here — they live in `gervazy`.

## Purpose

When toto needs to call an external service (webhook, LLM provider, SMTP relay), the operator registers an `ApiConnector` with the base URL and auth config. Secrets (API keys, SMTP passwords) are stored as `gervazy.EncryptedSecret` and referenced by FK — the connector config itself never contains plaintext secrets. `steven` subclasses `ApiConnector` for LLM providers. `EmailService` wraps SMTP config for community notification emails.

## Models

- `ApiConnector` — abstract base for outbound API connectors. Fields: `name`, `slug`, `base_url`, `auth_type` (`none` / `api_key_header` / `bearer_token` / `query_param`), `auth_config` (JSON schema-validated, rejects secret-like keys), `api_secret` (FK to `gervazy.EncryptedSecret`), `signing_key` (FK to `gervazy.EncryptedPrivateKey`), `owner` (FK to `auth.User`), `is_active`.
  - Validation rejects any `auth_config` key whose name looks like a secret (`api_key`, `token`, `password`, etc.) — enforces the pattern that secrets must go to gervazy.
- `Connector` — concrete subclass of `ApiConnector`. Adds: `description`, `tags`.
- `EmailService` — SMTP email sender configuration. Fields: `name`, `host`, `port`, `use_tls`, `username`, `smtp_secret` (FK to `gervazy.EncryptedSecret`), `from_email`, `is_active`, `owner`.

## Key coupling

- `gervazy.EncryptedSecret` — `api_secret` and `smtp_secret` are encrypted references, never plaintext.
- `steven.AgentConnector` subclasses `ApiConnector` to add LLM-specific fields (model, system prompt, temperature).
- `workflows` can trigger email delivery via an `EmailService` connector.

## Dependencies

- `gervazy` — EncryptedSecret and EncryptedPrivateKey for api_secret / signing_key
