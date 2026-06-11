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

`deploy.py` adds a `neo4j` service automatically whenever `BUILD_NEO4J=1` (or
`services.neo4j: true`) is set in the deployment config. It starts with the stack:

```bash
python portal/scripts/deploy.py deployment/portal_max.yaml up
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

`portal/portal/settings.py` enables Ravioli when `BUILD_NEO4J=1`. It reads:

| Setting | Env var | Default |
|---|---|---|
| `NEO4J_URI` | `NEO4J_URI` | `bolt://neo4j:7687` |
| `NEO4J_USER` | `NEO4J_USER` | `neo4j` |
| `NEO4J_PASSWORD` | `NEO4J_PASSWORD` | `neo4j-admin` |
| `RAVIOLI_RICH_INGRESS` | `RAVIOLI_RICH_INGRESS` | `0` (thin) |
| `RAVIOLI_DEFAULT_MAX_HISTORY` | `RAVIOLI_DEFAULT_MAX_HISTORY` | `3` |

`RAVIOLI_ENABLED` is set to `True` only when `BUILD_NEO4J=1`.

`RAVIOLI_DEFAULT_MAX_HISTORY` is the **default keep** for the manual "Prune history"
review (newest `:HISTORICAL` snapshots kept per node) — not an automatic cap; the
sync/export path never prunes on its own.

**Ingress is thin by default** (no Neo4j seeding — fast). Set `RAVIOLI_RICH_INGRESS=1`
to seed sample graph data during `ingress_ravioli` / `ingress_bento` (slow — many
Neo4j round-trips). Per-run override: `manage.py ingress_bento --rich` / `--thin`.

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

**`RAVIOLI_ENABLED is False`** — `BUILD_NEO4J` is not set to `1`, so `settings.py`
never sets `RAVIOLI_ENABLED = True`. Set `BUILD_NEO4J=1` in your deploy config's
`env:` block (or environment).

**First login to a fresh container** — Neo4j 5 requires a password change on first use
when `NEO4J_AUTH` is set. If you hit an auth error, open http://localhost:7474, log in
with the configured credentials, and follow the prompt (or use `NEO4J_AUTH=none` to
disable auth entirely in dev).
