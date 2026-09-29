# toto.vault

File storage for every host: buckets, directories, gateways, versioning,
encryption at rest, quota metering — and, since 8/2026, **remote buckets**:
a bucket whose bytes live on S3-compatible storage or on another federated
Toto host. This README covers the remote half; the local machinery predates
it and is documented in the code.

## Backends

`Bucket.storage_backend`:

| backend | bytes live | listing | who bills |
|---|---|---|---|
| `local` (or blank) | this host's `VAULT_ROOT`/`MEDIA_ROOT` | DB rows | `storage.gb_day` levy, owner |
| `s3` | an S3-compatible service (provider preset + `storage_config`) | DB rows | levy, owner (this host pays the provider) |
| `remote_toto` | a paired Toto host's exported bucket | **mirrored** DB stub rows (`VaultFile.origin="mirror"`) | the exporting host — mirrored rows are levy-exempt |

Every byte crosses one seam (`storage_backends.py`): `read_file_bytes` /
`open_file_stream` / `persist_upload`, backed by a per-bucket driver.
Content-rewrite surfaces (editors, zip, encrypt, versions) are LOCAL-only,
guarded by `access.is_local_content` + `access.local_content_q()`; downloads
stream from anywhere. Uploads into a `remote_toto` bucket are refused — its
rows come from the mirror.

## The bucket link (peering)

The platform's one host-to-host data channel (the row-replication `datalink`
app is parked in `limbo/datalink/` — see its `PARKED.md`). Two directional
models in `peering.py`:

- **`BucketGrant`** — "that peer may use this bucket." Stores only an api-key
  HASH plus a high-entropy `magic_token`. Capabilities are `may_list` /
  `may_download` / `may_upload` / `may_delete`, ALL default False — **an
  empty capability set denies**; copy is download+upload, no flag of its own.
  Grants expire in 7 days by default and are extended deliberately.
- **`BucketPeer`** — "we mount that host's bucket." Holds the secret we
  present, Fernet-sealed under `FIELD_ENCRYPTION_KEY`, plus job-stamped
  reachability (`last_ok_at`/`last_error`).

No vault model may carry a field named `uid` — `backup_engine` selects rows
for signed, pullable archives purely on that name; `VaultConfig.ready()`
enforces it structurally.

### Pairing flow (superuser admin, v1)

1. Exporting host: add a **Bucket grant** → the save message shows a base64
   **pairing code** ONCE (`{"v":1, grant_uid, magic_token, api_key, bucket,
   rights}`). Rotate = the "Rotate api key" action, new code shown once.
2. Mounting host: add a **Bucket peer** → pick the federated host (from SSO
   pairing rows) or type a URL, paste the code. The save probes the manifest
   once and stamps the result.
3. Mounting host: create a Bucket with backend `remote_toto` and select the
   peer. `storage_config` stays EMPTY — the peer FK is the whole transport
   identity, so listings can never leak a URL or a token.

The **Remote** tab (`/vault/remote/`) is that door, shipped: operator-gated
(`is_staff or is_superuser`), listing every S3 and mounted bucket with health
read from stamped columns only. Configuration still happens in the admin; the
credential model that lets it move out of there is the next stage.

## Peer API (server half — `peer_views.py`)

Under `/vault/peer/<grant_uid>/<magic_token>/…`: `manifest/`, `files/`
(keyset-paged list / upload), `files/<key>/` (meta / delete),
`files/<key>/download/` (stream, HEAD). Auth: the unguessable path segment
is the cheap check; PBKDF2 verification of `X-Vault-Api-Key` runs strictly
last. Unknown pair → 404 (anti-enumeration); held-but-expired grant → 403
with a sentence. Whole-bucket export — directory ACLs do NOT cross hosts.
Encrypted non-PDF files answer 409: they are sealed under the exporting
host's local salt and would be garbage anywhere else.

## Mirror (`mirror.py`)

`BucketRefreshRun` walks the peer's listing page by page (one transaction
per page, counters and rows together), upserts stubs (`file.name` = the
remote key — what the driver dereferences), prunes unseen mirror rows
ROWS-ONLY (a refresh can never delete anything at the peer), and stamps
`Bucket.last_refreshed_at`. No page render ever probes a peer; the metrics
page's Remote card reads stamps and says "never checked" when nobody has.
Mirror rows refuse move/rename/delete ("change it on the origin host").

## Transfers (`transfer.py`, `transfer_runner.py`)

Copies stay synchronous local→local; any non-local endpoint becomes a
`TransferRun` on a worker (`refuse-don't-inline`: no worker → 503 naming
BUILD_WORKFLOWS). Per file: portability check → read → size cap →
recompute sha256 (mismatch = skip) → scan on arrival (`door="transfer"`) →
key policy → land → meter (`storage.transfer_mb`, idempotency key
`vault.transfer.mb:{run}:{src}`). The cursor is written in the same
transaction as the row it advances past, so a killed run resumes instead of
restarting and can never double-copy or double-bill. **Partial completion is
SUCCESS with skips**; retry mints a NEW run from the remainder plus skipped
pks. Panel: `/vault/transfers/`.

## Flags

| flag | effect |
|---|---|
| `VAULT_EXTERNAL_BUCKETS=False` | local-only host: driver factory refuses non-local buckets, admin hides the storage fieldset, peering admins vanish, the Remote tab 404s |
| `VAULT_OUTBOUND_ALLOWED_HOSTS` | hosts the outbound guard permits regardless of address or scheme — the internal-MinIO case |
| `VAULT_OUTBOUND_ALLOW_PRIVATE` | dev/CI hatch: allow plain http |
| `BUILD_WORKFLOWS` off | refresh + transfer dispatch refuse by name |
| `BUILD_ANTIVIRUS` off | scans degrade to clean-but-unscanned (façade) |

## Circles — a file kept to some people (2026-09-29)

A file may be kept to **circles** (`VaultFileCircle`; a circle is a
`socialhub.Community` with `is_circle`, and its members are whoever
`Person.communities` says). The rule is `toto.socialhub.circle_access`, read
by `access.may_read` and `filetree.accessible_files` — the same two doors
every reader already used, so sheets, decks, the download URL, the versions
and lock endpoints, the browser, attachments and every picker follow it with
no rule of their own:

* a file with **no circle** is what it always was — owner, public, bucket
  owner, a folder's ACL;
* a file **with circles** is read by their members, its owner and superusers,
  and by nobody else: not through the public flag, not through a folder's
  ACL. A circle both keeps and grants;
* a hidden file is a missing file (404), and its history and lock with it.

Changing them: `circles.file_access` — `files/<pk>/access/`, for the owner
or a superuser, reached from the app that shows the file (a sheet's or a
deck's toolbar: "Who can read") with `?next=` back. An owner is offered the
circles they are in. Every change is on the audit chain
(`VAULT.FILE.CIRCLES_CHANGED`: before, after, `open`). A circle that still
keeps a file cannot be deleted (PROTECT). Tests: `tests_circles`.

## Tests

Vault Django test modules run only where a gate stanza names them (the
library pytest suite does not collect them): `tests`, `tests_access`,
`tests_api`, `tests_purge`, `tests_hardening`, `tests_peering`,
`tests_peer_api`, `tests_mirror`, `tests_transfer`, `tests_remote_ui`,
`tests_transfers_ui`, `tests_outbound`, `tests_bucket_transition`,
`tests_remote_page`, `tests_circles` — wired in zenobia's gate, core four in placidia's.
The two-host harness is a loopback: `peer_client._http` patched into
Django's test client against the real peer views (one DB, clearing's
pattern).


## The four tabs

`templates/vault/base.html` is the shell every vault page extends; the view sets
`active_tab` and the bar renders itself (the `antivirus/base.html` idiom).

| tab | url | holds |
|---|---|---|
| Files | `vault:public_list` | the tree you work in — **no zip action** |
| Metrics | `vault:metrics` | aggregate and per-bucket figures |
| Remote | `vault:remote_buckets` | S3 and mounted buckets, health from stamps; operator-only |
| Archive | `vault:archive` | the same tree again, carrying the zip actions |

Archive is a second tree rather than a shared partial for the reason
`antivirus/_scan_tree.html` gives: its rows carry an ACTION and a selection, and
the reading tree must not grow either. It is built from exactly the queryset
`CreateZipView` accepts — same bucket, not encrypted, local content only — so no
row can show a button that cannot work.

## Outbound safety

`outbound.py` guards the two places this host fetches an operator-supplied URL:
`BucketPeer.base_url` (via `PeerClient.__init__`) and the S3 `endpoint_url` (via
`_build_client`). One function, both sinks. It refuses userinfo/backslash URLs
that make `urlsplit` and `requests` disagree about the host, refuses any address
that resolves private/loopback/link-local/reserved, and refuses plain http —
with `VAULT_OUTBOUND_ALLOWED_HOSTS` as the explicit escape hatch.

An **unresolvable** host is allowed through on purpose: a name that does not
resolve cannot be connected to either, and refusing on DNS failure would redden
every offline test suite. **DNS rebinding is a known residual** — the guard
resolves at check time, and pinning the checked IP for the connection would mean
rewriting `peer_client._http()` and a botocore endpoint resolver.
