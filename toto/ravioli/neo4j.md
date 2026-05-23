# Neo4j for Ravioli

## Quick start (local dev — Docker)

```bash
docker run -d \
  --name neo4j-dev \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/neo4j-admin \
  -e NEO4J_PLUGINS='["apoc"]' \
  -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
  neo4j:5
```

Then set in your `.env` or shell:

```
RAVIOLI_ENABLED=true
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4j-admin
```

Browser UI: http://localhost:7474

---

## Docker Compose (studio)

Neo4j is already declared as the `neo4j` service in `deploy/studio/docker-compose.yaml`.
It starts automatically with the stack:

```bash
docker compose -f deploy/studio/docker-compose.yaml up
```

The web container connects to it via `bolt://neo4j:7687` (Docker internal DNS).
When running Django outside Docker against the same container, `connection.py` falls back
to `bolt://127.0.0.1:7687` automatically, so no extra config is needed as long as port
7687 is exposed.

Required `.env.studio` keys:

```
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4j-admin   # change in production
```

---

## Settings wiring

`settings_builder.py` enables Ravioli when `services.neo4j` is truthy in the YAML config.
It reads, in priority order:

| Setting | Env var | YAML key | Default |
|---|---|---|---|
| `NEO4J_URI` | `NEO4J_URI` | `neo4j.host` → `bolt://<host>:7687` | `bolt://neo4j:7687` |
| `NEO4J_USER` | `NEO4J_USER` | `neo4j.user` | `neo4j` |
| `NEO4J_PASSWORD` | `NEO4J_PASSWORD` | `neo4j.password` | `neo4j-admin` |

`RAVIOLI_ENABLED` is set to `True` only when the `neo4j` service block is present in the config.

---

## Troubleshooting

**`Cannot resolve address neo4j:7687`** — Django is running outside Docker and the
hostname `neo4j` can't be resolved. Set `NEO4J_URI=bolt://127.0.0.1:7687` in your
local `.env`.

**`Connection refused`** — Neo4j is not running. Start it with the Docker command above
or bring the compose stack up. Check with:
```bash
docker ps | grep neo4j
docker logs neo4j-dev
```

**`RAVIOLI_ENABLED is False`** — the `neo4j` service block is missing from your deploy
YAML config, so `settings_builder.py` never sets `RAVIOLI_ENABLED = True`. Add it or
set `RAVIOLI_ENABLED=true` directly in your environment.

**First login to a fresh container** — Neo4j 5 requires a password change on first use
when `NEO4J_AUTH` is set. If you hit an auth error, open http://localhost:7474, log in
with the configured credentials, and follow the prompt (or use `NEO4J_AUTH=none` to
disable auth entirely in dev).
