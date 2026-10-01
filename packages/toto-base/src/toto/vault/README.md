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

### Pairing flow (v1)

The guided path is Storage → Management ("Another Zenobia: share and connect"
below), which does all three steps from the two Management pages and shows
the code as a QR code too. The exporting side is there ONLY (2026-10-01): the
**Bucket grant** admin is a record and mints no code — no add, no "Rotate api
key" (it showed the code in an admin message, which Django's message storage
may keep in a cookie). Every field there is read-only but Active; unticking it,
or the "Revoke the selected shares" action, is Management's Revoke
(`share_views.revoke_share`, on the audit chain), and a revoked share stays
revoked. The changelist says where shares are made and links there.
Tests: `tests_grant_admin`.

1. Exporting host: Storage → Management → a bucket's **Share** → the base64
   **pairing code** shown ONCE (`{"v":1, grant_uid, magic_token, api_key,
   bucket, rights}`, plus `expires_at` and `host`). Rotate key there too, the
   new code shown once.
2. Mounting host: Management's **Connect a bucket from another Zenobia** — or
   add a **Bucket peer** in the admin → pick the federated host (from SSO
   pairing rows) or type a URL, paste the code. The save probes the manifest
   once and stamps the result.
3. Mounting host, admin path: create a Bucket with backend `remote_toto` and
   select the peer. `storage_config` stays EMPTY — the peer FK is the whole
   transport identity, so listings can never leak a URL or a token.

The Remote tab that listed S3 and mounted buckets (and had full-page create
forms for them) folded into **Storage → Management** (2026-09-30, see "The
Management tab" below): every bucket, remote ones with their health read from
stamped columns only, and a connection Test per row.

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
| `VAULT_EXTERNAL_BUCKETS=False` | local-only host: driver factory refuses non-local buckets, admin hides the storage fieldset, peering admins vanish, Management's New bucket offers this server only |
| `VAULT_OUTBOUND_ALLOWED_HOSTS` | hosts the outbound guard permits regardless of address or scheme — the internal-MinIO case |
| `VAULT_OUTBOUND_ALLOW_PRIVATE` | dev/CI hatch: allow plain http |
| `VAULT_FIELD_KEY_PERSISTENT=True` | the host loads `FIELD_ENCRYPTION_KEY` from somewhere other than the environment: sealing is allowed |
| `VAULT_PURGE_INLINE=True` | Delete's purge runs in-process instead of on a worker (tests, dev) |
| `VAULT_PURGE_STALL_MINUTES` (30) | a bucket still being deleted, with no reason stamped, this long after Delete was last confirmed reads "Deletion may have stopped" and offers Delete again |
| `VAULT_AWS_REGIONS`, `VAULT_OVH_REGIONS` | replace / extend the region lists Create offers |
| `BUILD_WORKFLOWS` off | refresh + transfer dispatch refuse by name |
| `BUILD_ANTIVIRUS` off | scans degrade to clean-but-unscanned (façade) |

## File types: accepted and refused (2026-09-30)

A file's type is `VaultFile.detect_type(mime, filename)`: the extension
first (`_EXT_MAP`), then the browser's MIME type, and `text` when neither
names anything. The types a file can be: PDF, image, HTML, text, Markdown,
JSON, NeoJSON, YAML, XML, LaTeX, bibliography, CSV, SVG, audio, video,
Python, a Primula sheet, a deck (`.pxml`) and a zip archive. Only svg, html,
xml, json, pdf and pxml are read by the screener (`scanning.SCANNABLE_TYPES`);
every other type is stored unscreened and recorded as unscreened.

Refused:

* **Microsoft Office files, always, on every host** (the owner, 2026-09-30:
  ".docx, .xlsx or .pptx ---> REJECT. NO Microsoft here."). The OOXML formats
  — .docx .xlsx .pptx and .docm .xlsm .pptm .dotx .xltx .potx — and the old
  binary ones — .doc .xls .ppt — are not uploaded, imported, previewed or
  converted, by any door, and no door advertises them. The refusal says why
  and what to save the file as instead (PDF, HTML, Markdown, CSV or
  OpenDocument). Before this rule an OOXML upload met the XML screener's
  generic refusal only by accident (its MIME type contains "xml").
  One rule, `models.upload_refusal(name, file_type=, content=, mime=)`:
  `is_office_file` by extension (`OFFICE_EXTENSIONS`), by content and, only
  where a door has no bytes to judge, by declared type (msword,
  officedocument, ms-excel, ms-powerpoint, openxmlformats — Windows declares
  ms-excel for every .csv where Excel is installed); the content — an OLE2
  compound file (`D0 CF 11 E0`) or a zip holding `[Content_Types].xml` and a
  `word/`, `xl/` or `ppt/` part — so a .docx renamed .zip is refused and an
  .odt (a zip without that part) is not; then the host's refused types. The
  doors that ask it: the gateway upload, the API upload and create, the peer
  upload, rename (the page's and the API's PATCH), New file, a transfer's copy
  (`transfer_runner`, skipped with the sentence), a copy between two local
  buckets (refused, naming the files), a mirror refresh (the peer's Office
  rows are never stubbed, and old stubs are pruned), a git pull
  (`toto.repo.sync.import_worktree` leaves them in the worktree and lists them
  under `refused`), a capsule transfer and the Capsule's own ways in — the
  desk's upload, the API's files/put and a job's staged inputs
  (`toto.anastasia`) — and on zenobia the wiki's image upload and zip import.
  Tests: `tests_office_refusal`.
* **OpenDocument is not Microsoft** — .odt .ods .odp are unaffected and keep
  today's behaviour.
* Whatever a host lists in `VAULT_REFUSED_FILE_TYPES` (e.g. `{"latex"}`),
  refused at every door that assigns a type; rows that predate the ban keep
  working.
* Whatever the screener refuses (a hostile SVG, HTML, XML, JSON, PDF or deck):
  the write stops and the author gets a sentence, never a repaired file.

## Clearances — a bucket kept to some people (2026-09-30)

Clearances go on groups, never on items: in the vault the group is the
**bucket** (`BucketClearance`; a clearance is a `socialhub.Clearance`, named
after what it opens, and its holders are whoever `Person.clearances` says). A
file has one bucket; a file in no bucket is never kept. The rule is
`toto.socialhub.clearance_access` (`group_gate` / `group_hidden`, with the
file's bucket as its groups), read by `access.may_read`,
`access.gate_by_bucket` / `access.bucket_hidden` and
`filetree.accessible_files` — the doors every reader already used, so sheets,
decks, the download URL, the versions and lock endpoints, the browser, the JSON
API, the owner's own doors (rename, move, delete, encrypt, copy, zip),
attachments and every picker follow it with no rule of their own. So do the
editors' own owner-only doors (2026-09-30): the ACE editor's open, save and
delete and its sync socket, the writer (cyprian), memo, primula and sketch ask
`gate_by_bucket` before `owner=` — an owner without the clearance gets 404
there too (`toto.editor.tests_clearance`, `toto.cyprian.tests.test_clearance`),
and since 2026-10-01 so does the NeoJSON editor's open, save and load into
Neo4j (`toto.neo_editor.tests_clearance`):

* a file in a bucket with **no clearance** is what it always was — owner,
  public, bucket owner, a folder's ACL;
* a file in a bucket **with clearances** is read by superusers and by whoever
  holds one of the bucket's clearances, and by nobody else: not its owner, not
  through the public flag, not the bucket's owner, not a folder's ACL. A
  clearance both keeps and grants;
* a hidden file is a missing file (404, absent from lists and counts — the
  bucket's own page included), and its history and lock with it.

Changing them: only a **superuser on the Superuser plan** (2026-10-01,
`plan_gate.superuser_plan_holder` — the account alone is refused, as by the
wiki's `may_keep`), in the "Clearances" section of the bucket's page
(`metrics/<slug>/`), which posts to `clearances.bucket_clearances`
(`buckets/<slug>/clearances/`); whoever may see the bucket's page sees its
clearances, read-only (its owner and a superuser without the plan are told
"This needs a superuser on the Superuser plan.", 403; anybody else gets the
page's 404). Every change is on the audit chain
(`VAULT.BUCKET.CLEARANCES_CHANGED`: before, after, `open`, the bucket). A
clearance that still keeps a bucket cannot be deleted (PROTECT). There is no
per-file door any more. Tests: `tests_clearances`, `tests_more_clearances`.

Seeing them all: the vault's **Clearances** tab (`clearances/`,
`clearance_tab.py`), for a superuser on the Superuser plan only
(`plan_gate.superuser_plan_door`; the tab shows by `vault_flags.superuser_plan`).
Every bucket — personal ones and those being deleted included — with the
clearances keeping it and, per clearance, its holders by display name (the
first 20, then "and N more"); a bucket kept by none reads as open. A table on
md+, a card per bucket below, 25 buckets a page, and three counters (all /
kept / open) that are also the filter (`?show=`). Read-only: each bucket's
name leads to its page, where its clearances are set. A page costs the same
queries however many buckets, clearances and holders it shows. Tests:
`tests_clearance_tab`.

## Versions and the editing lock: who reads, who writes (2026-09-30)

`version_views.py` serves every editor's history and lock (cyprian, memo,
primula, sketch, the ACE editor). Its doors used to share one gate that read
"may work with this file" off the folder and the public flag, and it let far
too many people in: a folder whose ACL is empty (every folder, until someone
fills it) opened another member's file to any signed-in account, and a public
file let every reader take the lock, cut a version and restore an old body
over its owner's work. The doors are split now, and neither half is a new rule:

| door | rule | refused |
|---|---|---|
| the history (`version_list`) | `access.may_read`, the download door's — superuser, owner, public, the bucket's owner, a folder ACL that names you — or a writer below | 404 |
| take, beat and release the lock; cut a version; restore one | `access.may_write`, the rule the editors' save doors apply: the owner, the app that lends the file out (`VaultAccessPlugin`, cyprian's wiki team), or a superuser on the Superuser plan — asked after the read gate | 403 |

* Both put the bucket's clearances first: a file hidden by its bucket is
  missing on every door, to its owner and to a lending app too.
* Staff is nobody special here any more; a superuser without the plan reads
  (as `may_read` lets them) and writes only their own files.
* 403 is for a file the caller may see, 404 for one they may not, as on every
  vault door. A reader is not told 423: they are not a writer who has to wait.
* The history answers `can_write`, and the versions panel
  (`oya/_file_versions.html`, `oya/file_versions.js`) turns into a reader's
  when that is false, when the page passes `can_write=False`, or when a lock
  door refuses: the history and who is editing, no claim, heartbeat, beacon,
  "Save version" or "Restore".
* On the audit chain: a refused cut or restore is recorded like any vault
  write (`FILE_EDITED` / `FILE_RESTORED`, `success=False`), and a refused lock
  claim as `FILE_LOCK_REFUSED` (403 and 404 only — a granted claim, a 423 and
  every heartbeat stay off, being an editor opening).

Tests: `tests_version_doors`, `tests_more_version_views`.

## The trash (2026-10-01)

In short, for a member: a deleted file waits in the Trash tab for
`VAULT_TRASH_DAYS` days (30), where its owner restores it or deletes it for
good; after that the nightly purge deletes it. While it waits it STILL
counts — against the bucket's `storage_quota_mb` and in the daily
`storage.gb_day` levy — so the trash is never a free place to keep bytes.
A file in a bucket mounted from another Zenobia has no trash: its delete is
immediate and permanent, and every dialog that offers it says so.

Deleting a vault file moves it to the trash. The file keeps its bytes, its
versions, its bucket (so the bucket's clearances still keep it) and its key;
it leaves its folder (`directory` cleared, the folder kept in `trashed_from`
for the restore) and is stamped `trashed_at` / `trashed_by`. It is kept
`VAULT_TRASH_DAYS` days (30; `models.trash_days()`). Wiki pages have no trash.

**How a trashed file disappears: the default manager hides it.**
`VaultFile.objects` is `LiveFileManager` (`trashed_at IS NULL`), and so are the
reverse relations built from it (`bucket.files`, `directory.files`).
`VaultFile.all_objects` sees every row and is the base manager
(`Meta.base_manager_name`), so forward foreign keys (`FileVersion.file`, a
lock, an app's pointer), `refresh_from_db`, saves and the deletion collector
still reach a trashed row. The alternative — a filter at every door — was
refused: the vault has well over a hundred `VaultFile` queries across the
library and the host, and a door that forgot the filter would LEAK the file;
with the manager, a door that forgets `all_objects` HIDES it instead, which
fails closed. The listing, the JSON API, the picker, the peer API (manifest,
listing, detail, download), the editors, the download door, the services
door, the zip builder and every other app reading `VaultFile.objects` hide it
without a line of their own.

What asks for `all_objects` by name, because the trash must not be a free
hiding place — its bytes are still held:

* the storage levy and the plaintext levy (`taxes.py`, `storage.gb_day`,
  `security.plain_gb_day`) and mana's preview of the latter;
* the bucket figures against `storage_quota_mb` (the listing's per-bucket
  usage, the metrics page's per-member rows, `storage_adapters.file_totals`);
  the upload quota (`VaultQuotaPolicy`) counts usage EVENTS, which a trash
  never removes;
* the bucket purge (`bucket_lifecycle.purge_bucket`) — a trashed file is still
  in its bucket, and `bucket` is PROTECT;
* the admin (with a "trashed" filter).

**Joins skip the manager.** A lookup through another model
(`WikiAsset.objects.filter(vault_file__…)`, `Bucket…annotate(Count("files"))`)
does not apply `VaultFile.objects`; a join that shows a file must say
`…trashed_at__isnull=True` itself (the wiki's image render does). Counts
through a join include the trash, which is what a usage figure wants.

**Keys.** The (bucket, key) rule binds live files only
(`vault_one_live_file_per_key`, a conditional unique constraint replacing
`unique_together`): a new upload may take a trashed file's name, and the
restore finds a free key if its own was taken meanwhile.

**Remote buckets have no trash.** A file in a mounted bucket from another
Zenobia (`remote_toto`), and any mirror row, names the PEER's file; this
server cannot hold its bytes for a restore. `VaultFile.can_be_trashed` is
False there, and the doors keep their immediate delete, whose dialog says
it is permanent. A peer's own DELETE on an exported bucket
(`peer_file_detail`) also stays a purge on this host.

**One door: `trash.remove_file(vault_file, by=, request=, door=)`.** Every
delete a member reaches goes through it: the listing's Delete
(`DeleteFileView`, answers `trashed: true|false`), the API's
`DELETE /vault/api/files/<key>/`, the ACE editor's delete, primula's sheets,
memo's decks, sketch's drawings, mandragora's notebooks, an ambrosia
workspace's file delete AND its Destroy (the folders go for good, the files to
the trash), and a git pull that deletes a tracked file (`toto.repo.sync`). It
trashes where `can_be_trashed`, deletes at once where not, and records the act
on the audit chain itself — `FILE_TRASHED` for a trash, distinct from a real
`FILE_DELETED` — then marks the request so `FileAuditMiddleware` does not add
the url's `FILE_DELETED` too. A refused delete (404) is still the middleware's
failed `FILE_DELETED`.

Doors that keep their behaviour, on purpose: a copy or transfer with the
"replace" policy (an overwrite the copier chose, not a delete), a mirror
refresh's prune (rows only), the bucket purge and the peer API's delete
(purges), `erase_user` (console), the admin (superusers), a wiki page's
image un-attach and kanban's attachment remove (the link only, never the
file), forum attachments (not vault files).

**The delete signal and a trashed row.** `signals.delete_file_on_disk` never
unlinks a trashed row's bytes while the delete can still roll back: it
unlinks them after the commit. `purge_file` removes them through the driver
anyway; the after-commit unlink is for cascades that bypass it (an erased
account), so they leave no orphan.

**Leaks closed through joins and forward keys** (they skip the manager):
`attach.readable` refuses a trashed file (the wiki's export list and every
attachment list), the wiki's image lists and backup, the git import (a trashed
file has left the repo), the metrics API and page (counts are live files;
sizes are stored bytes, trash included, with `trash_size_bytes` /
`trash_size` saying how much), the "buckets I have files in" tree, and
yamabiko (an owner-trashed copy is a missing copy: copied again, the trashed
row left alone).

Tests: `tests_trash` (hidden on each door; levy, figures and bucket purge
still count or take it; versions still find it; the key is free),
`tests_trash_doors` (each door trashes and records FILE_TRASHED; the remote
bucket's immediate delete; the signal; the figures; attachments).

### The Trash tab (`trash_views.py`, `vault:trash`)

Who sees what (`trash.trashed_for`): a member their OWN trashed files in
buckets whose clearances let them read — pessimistic, no owner bypass, so an
owner who lost a bucket's clearance loses its trash too; a superuser on the
Superuser plan every trashed file (a superuser without the plan is a member
here). Every door looks the file up in that same queryset, so a file one may
not see answers 404. Columns: name (and owner, for the superuser), bucket and
the folder it came from, size, trashed at, trashed by, days left
(`trash.days_left`, counting down from `VAULT_TRASH_DAYS`, which the page
states). A table on md+, cards below, the shared pagination.

* **Restore** (`trash.restore_file`, `FILE_RESTORED`) — to the folder it came
  from; to the bucket's root when that folder is gone (deleting a folder
  SET_NULLs `trashed_from`, so such a file cannot be told from one trashed at
  the root — both land there, and the message says where); when a live file
  in that folder has its name, or one in the bucket its key, it comes back as
  `name (restored).ext` (then `(restored 2)`, …) with a key to match, and the
  message says so. A bucket being deleted takes nothing back.
* **Delete for good** (`trash.purge_trashed`, `FILE_PURGED`) — through
  `purge.purge_file`: the row, its bytes and the version bodies only it cited.
  A modal whose checkbox must be ticked; the form carries `confirm=yes`, and
  the server refuses without it. A file an app still pins (`ProtectedError`)
  stays, and the page says so.
* **Empty my trash** — Delete for good for one's OWN trashed files, confirmed
  the same way; a superuser on the plan empties their own too, never
  everybody's.

A file in a bucket connected from another Zenobia never reaches the trash
(its delete is immediate, above); the page says so. The audit records carry
ids only, never a name. Tests: `tests_trash_page`.

### The nightly purge (`trash.purge_expired`, beat `vault-trash-purge`)

At 03:50 — before the 04:15 storage levy, so an expired file stops costing
that day — `tasks.purge_expired_trash` deletes for good every file trashed
more than `VAULT_TRASH_DAYS` ago (host setting from the environment, default
30; `VAULT_TRASH_PURGE=0` turns the beat entry off and keeps every trashed
file, and its cost). Through `purge.purge_file` in STRICT mode: bytes that
cannot be deleted keep the row, so a failed file stays in the trash (still
counted) for the next night, its reason logged; a file an app still pins
waits the same way; one failure never stops the rest. In batches of 200 pks
fixed at the start of the run, with a 20-minute budget — what is left waits
for the next night. Idempotent: a purged row is gone, and a second run finds
nothing due. Files of a bucket being deleted are left to the bucket purge,
which takes the trash of every age (`all_objects`). Audited per file as
`FILE_PURGED`, door `trash_expired`, no actor, ids only. Tests:
`tests_trash_purge`.

### Several files at once (`bulk.py`, `vault:bulk_trash`, `vault:bulk_move`)

The file list ticks a member's own files (a checkbox on each of their rows
and cards, "Select my shown files" for what the search and filters show) and
offers **Move to…** and **Move to the trash** for the selection. Each file is
checked on its own by its single-file door's rules and answered on its own —
`{"results": [{"id", "status", "reason"}], "done", "refused"}`, status
`trashed` / `deleted` / `moved` / `unchanged` / `refused` — never all or
nothing; what was refused stays ticked, listed with the reason. One audit
record per file, refusals included (`FILE_TRASHED` / `FILE_DELETED` /
`FILE_MOVED`, ids only), and the request is marked so the middleware adds
none; a request refused whole (no files, a destination that does not exist)
is the middleware's one failed record.

* **Move to the trash** — `DeleteFileView`'s rules: the member's own file, in
  a bucket whose clearances let them read (no owner bypass), never a mirror
  row; through `trash.remove_file`. A mounted remote bucket's file is deleted
  at once — only when the request says `remote=yes`, which the dialog sends
  after saying how many of the selection that is; without it such a file is
  refused, not deleted.
* **Move to…** — a bucket and a folder (`bulk.move_targets`: the member's own
  buckets they may fill, plus buckets where they have files, for moves inside
  those). Within the file's bucket, `MoveFileView`'s rules. Into ANOTHER
  bucket, also `bulk.may_move_into`: the member's own bucket, not being
  deleted, not hidden from them by its clearances, and both ends on this
  server's disk — there a move is the row changing buckets (the key changes
  if the new bucket has it; title, versions and owner stay). Between storage
  kinds it would be a transfer, which Copy files does; such a file is refused.

Bounded at `bulk.MAX_FILES` (500) files per request. Tests: `tests_bulk`.

## Buckets: types, custody and deletion (2026-09-30)

Storage → Management (Superuser plan) creates, edits, tests and deletes buckets.
This section is the foundation it stands on; the tab's views ask these modules
and never branch on a provider.

### The bucket model

| field | what |
|---|---|
| `name`, `slug` | the name is editable; the slug is fixed at Create (links, peer grants and the audit chain name it) |
| `owner` | **SET_NULL** (was CASCADE): deleting an account no longer deletes its buckets — a bucket holds other people's files, gateways and clearance keeping. Edit gives it a new owner |
| `created_by`, `created_at` | who made it in Management and when — never edited, blank on older rows |
| `storage_backend`, `provider`, `storage_config`, `peer` | fixed at Create: Edit (`bucket_lifecycle.update_bucket`) changes `name`, `owner`, `storage_quota_mb`, `ai_protected` only |
| `last_probe_at`, `last_probe_error` | the last connection test, stamped by an operator's click (never a page render); a mount's health stays on its `BucketPeer` |
| `deletion_requested_at`, `deletion_error` | the bucket is being deleted (and why the purge stopped, if it did) |

`VaultFile.bucket` is **PROTECT** (was SET_NULL): a bucket with files cannot be
deleted except by the purge, and no file ever ends with `bucket=None` — which
used to drop it out of its bucket's clearance keeping and leave its bytes behind.

### Kinds: the adapter interface

`storage_adapters.StorageAdapter` (a `BasePlugin` with its own registry,
autodiscovered from `plugins.storage_adapters` in `VaultConfig.ready`). The
vault's own, in `plugins/storage_adapters.py`:

| key | backend | Create asks for |
|---|---|---|
| `local` | local | nothing — this server's disk |
| `aws_s3` | s3 (preset `aws`) | bucket at the provider, region, optional prefix (blank = `vault/`), access key id + secret |
| `ovh_s3` | s3 (preset `ovh`) | the same; the region (a fixed list) picks the endpoint `https://s3.<region>.io.cloud.ovh.net` |
| `zenobia_remote` | remote_toto | the pairing code minted on the other Zenobia, and its address (or the one the code names) |
| `s3` | s3 | never created: describes, tests and deletes older S3 rows with a typed endpoint |

Each adapter answers `fields()` (the modal's inputs; `secret: True` marks the
ones never put back in a page, a draft, a log or JSON), `validate(data) ->
(config, secret)` (a `ValidationError` keyed by field, in sentences),
`probe_candidate(config, secret)` (the test before saving — an S3 bucket must
pass it, and `create` runs it again), `create(name, owner, actor, config, secret,
storage_quota_mb=, ai_protected=)` (one transaction: the bucket with `created_by`,
its sealed secret or pairing, `VAULT.BUCKET.CREATED`), `probe(bucket)` (bounded,
stamped), `describe(bucket)` (`target`, `status`, `health` and their labels,
the credential's hint — stamps only, no network, no secret) and
`destroy_plan(bucket)` (what Delete removes and what it keeps).
`StorageAdapter.creatable_adapters()` is what Create offers (remote kinds only
where `VAULT_EXTERNAL_BUCKETS` allows); `StorageAdapter.for_bucket(bucket)` is
the kind of an existing one. `zenobia_remote.decode_preview(code)` shows what a
code grants — host, remote bucket, rights, expiry, "already connected here" —
without saving anything; `validate` refuses a malformed, expired or
already-connected code. Pairing codes carry `expires_at` and, when the minting
door knows it, `host` (optional keys inside v1: older hosts ignore them, older
codes lack them and are shown as "not stated").

### Secret custody

| secret | where it lives | a DB dump alone | a DB dump + the deploy config |
|---|---|---|---|
| S3 access key id + secret (Management) | `BucketSecret.ciphertext`, Fernet under `FIELD_ENCRYPTION_KEY`; `hint` = last 4 of the key id | ciphertext only | **the keys** |
| a mount's api key | `BucketPeer.api_key_encrypted`, the same key | ciphertext only | **the key** |
| a mount's magic token, grant id | `BucketPeer` columns, plain (a URL segment by design — useless without the api key) | readable | readable |
| a grant we issued | `BucketGrant.api_key_hash` (PBKDF2) | a hash | a hash |
| S3 keys sealed under a storage PIN (`credentials.py`) | `RemoteCredential` | ciphertext | ciphertext — the PIN is in nobody's config |

The owner chose the field key for Management's S3 keys so a background job (the
purge) can use them with nobody at the keyboard; the storage-PIN custody stays
available and stronger. `get_bucket_storage` opens a bucket's `BucketSecret` per
call and hands the dict to the driver it builds — it dies with the driver, and
an explicit `credential=` (a PIN just opened) wins. A secret that no longer
opens (the key changed) raises `SealedCredentialUnreadable` with a sentence —
never a silent fall-back to the environment's keys. Buckets without a secret
keep the environment chain exactly as before.

Sealing refuses (`BucketSecret.seal`, and Create for `aws_s3`, `ovh_s3`,
`zenobia_remote`) when `models.field_key_configured()` is false: the key is
empty, or it is the per-process random fallback (the setting differs from the
environment's `FIELD_ENCRYPTION_KEY`) — a secret sealed under that is gone at
the next restart. A host that loads the key from a secrets file says so with
`VAULT_FIELD_KEY_PERSISTENT = True`.

Never shown after Create: secrets are not rendered, not kept in a draft, not
logged, not in JSON, not in audit metadata (`bucket_lifecycle.snapshot` is the
only shape a bucket is recorded in) and not in the admin (`BucketSecret` is not
registered; the bucket's page shows the hint). A peer transport error quotes
the URL it called, which carries the magic token: `PeerClient` redacts the
token, the grant id and the key from every stamp and message.

### Pairing-code trust

A pairing code is a live credential: whoever pastes it can do what the grant
allows (rights are ticked on the exporting side, all off by default; it expires,
7 days by default). The mounting side trusts nothing in it but the grant's
identity: the other host's address is SSRF-guarded (`outbound.assert_outbound_allowed`)
whether typed or taken from the code, the rights shown are advisory (the
exporting host enforces them), and the first probe's failure is stamped on the
pairing rather than undoing it.

### The SSRF guard

Every address this host calls is checked at the door as a sentence and again at
the call: an OVH endpoint (from the region list, never typed) and a peer's base
URL. AWS has no endpoint (default routing). See *Outbound safety* below for the
policy and its known residual (DNS rebinding).

### Delete

`bucket_lifecycle.request_deletion(bucket, actor, confirm_name=...)` — the typed
name must match. It marks the bucket (`deletion_requested_at`), records
`VAULT.BUCKET.DELETE_REQUESTED`, and after the commit hands `purge_bucket` to a
worker (`tasks.purge_bucket_task`; ids only, never a credential). No worker →
`deletion_error` says so and nothing is deleted; `VAULT_PURGE_INLINE = True`
runs it in-process (tests, a dev server). From the mark on, nothing new lands
in the bucket (`persist_upload` refuses with a sentence, `VaultFile.save` raises
`BucketClosed` for a file new to it, pickers leave it out), Edit refuses, and
lists show it as being deleted.

What the purge destroys:

* **this server / S3:** every file through `purge.purge_file(strict=True)` — the
  row, its bytes or its S3 object, and the bodies of its saved versions that no
  other file's version cites (`VersionBlob`, which has no link back to a file),
  through ONE driver built for the bucket (its sealed key opened once for the
  job). Strict: bytes that will not go (a key without `s3:DeleteObject`, a
  deactivated key, an unwritable disk) raise inside the file's transaction, so
  its row stays and the purge stops with the reason — a bucket is never recorded
  as deleted while its objects are still at the provider;
* then the bucket and what cascades from it: folders, upload gateways, clearance
  rows, grants to other hosts (they lose access), refresh runs, the sealed
  secret, a host app's rows that hang off it (echoes, workspaces);
* **another Zenobia:** its listing rows are deleted **as rows** — never
  `purge_file`, which would send a DELETE to the other host — then the bucket,
  then its `BucketPeer` when no other bucket uses it.

What it never touches: the files on another Zenobia; the S3 bucket itself and
any object this vault did not store in it; the other host's grant (revoke it
there). A file another app still holds (a PROTECT foreign key) stops the purge
before the bucket goes: the bucket stays marked, its clearance keeping stays,
`deletion_error` names the files, `VAULT.BUCKET.DELETE_FAILED` is recorded, and
confirming Delete again resumes. The finish is `VAULT.BUCKET.DELETED` (files
deleted, pairing removed), credited to whoever confirmed.

A purge never dies silently. One worker run purges for at most
`bucket_lifecycle.PURGE_BUDGET_SECONDS` (600) and queues the next run, so none
comes near the task's soft limit (1500 s) or the host's hard one; the soft limit
is re-raised, never counted as "one failed file"; a run stops after
`PURGE_MAX_FAILURES` (20) refused files; and whatever escapes the job — the time
limit, an error, the worker being stopped — is stamped on `deletion_error` and
recorded as `VAULT.BUCKET.DELETE_FAILED` (`bucket_lifecycle.stop_purge`). What no
code can see (a worker killed, a restart mid-purge, a lost queue message) is
what `VAULT_PURGE_STALL_MINUTES` is for: past it, the bucket reads "Deletion may
have stopped" and offers Delete again, which resumes (the purge is idempotent,
and `deletion_requested_at` is the last confirmation).

### Ownerless buckets

An account's deletion (or `erase_user`) leaves its buckets without an owner.
Such a bucket grants nothing through "the bucket's owner" (every check compares
a real user's pk; the anonymous arm never reaches it) and nothing crashes on it:

* `models.personal_bucket(user)` never hands out a `personal-<username>` bucket
  that lost its owner or changed hands (or is being deleted) — it takes the next
  free `personal-<username>-<n>`; the vault API, the new-file picker and aralia
  use it;
* writes that need an owner refuse with a sentence: a mirror refresh (stubs are
  owned by the bucket's owner), a peer upload into an exported bucket (409), an
  empty file created in it, an echo to or from it (yamabiko's
  `endpoint_refusal`);
* doors that create folders in a shared bucket (the forum's rooms, the wiki's
  images, the connectors' archive) fall back to a superuser, and a whitelist is
  never left empty (empty means everybody);
* the pages show "—" for the owner; a superuser on the Superuser plan gives it
  one in Management (`VAULT.BUCKET.UPDATED`).

Tests: `tests_storage_adapters`.

## Tests

Vault Django test modules run only where a gate stanza names them (the
library pytest suite does not collect them): `tests`, `tests_access`,
`tests_api`, `tests_purge`, `tests_hardening`, `tests_peering`,
`tests_peer_api`, `tests_mirror`, `tests_transfer`, `tests_remote_ui`,
`tests_transfers_ui`, `tests_outbound`, `tests_bucket_transition`,
`tests_remote_page`, `tests_clearances`, `tests_storage_adapters`, `tests_clearance_tab`, `tests_management`, `tests_share_connect`, `tests_trash`, `tests_trash_doors`, `tests_trash_page`, `tests_grant_admin` — wired in zenobia's gate, core four in placidia's.
The two-host harness is a loopback: `peer_client._http` patched into
Django's test client against the real peer views (one DB, clearing's
pattern).


## The tabs

`templates/vault/base.html` is the shell every vault page extends; the view sets
`active_tab` and the bar renders itself (the `antivirus/base.html` idiom).

| tab | url | holds |
|---|---|---|
| Files | `vault:public_list` | the tree you work in — **no zip action** |
| Metrics | `vault:metrics` | aggregate and per-bucket figures |
| Clearances | `vault:clearances_tab` | every bucket, the clearances keeping it, their holders; Superuser plan only |
| Management | `vault:manage` | every bucket; New bucket, Edit, Test, Delete, Share, Connect a bucket from another Zenobia; Superuser plan only |
| Archive | `vault:archive` | the same tree again, carrying the zip actions |
| Trash | `vault:trash` | one's trashed files (a superuser on the plan: everyone's); Restore, Delete for good, Empty my trash |

Archive is a second tree rather than a shared partial for the reason
`antivirus/_scan_tree.html` gives: its rows carry an ACTION and a selection, and
the reading tree must not grow either. It is built from exactly the queryset
`CreateZipView` accepts — same bucket, not encrypted, local content only — so no
row can show a button that cannot work.

## The Management tab (`manage_views.py`, 2026-09-30)

`/vault/manage/` — a superuser ON THE SUPERUSER PLAN only
(`plan_gate.superuser_plan_door` on every door: 403 before anything is looked
up, JSON for the JSON doors; the tab shows through the `superuser_plan`
filter of `vault_flags`, so it never shows to someone its doors refuse; on a
host without `toto.subscriptions` being a superuser is enough).

The list is every bucket — personal, ownerless and being-deleted ones
included — a table on md+ and a card per bucket below, paginated (20). Each
row: name and slug, the kind (the platform's cloud badge
`vault/partials/_bucket_badge.html` for a remote one, a Local pill otherwise,
and the adapter's title), the target and credential hint
(`adapter.describe`), owner, creator, created, the status (health from stamps,
being deleted and why a purge stopped, files and bytes) and the doors. No
render probes, opens a sealed key or calls out.

| door | url name | what |
|---|---|---|
| New bucket | `vault:manage_create` (POST) | kind from `StorageAdapter.creatable_adapters()` minus `GUIDED_KINDS` (another Zenobia has its own flow); name, owner (people search), quota, AI shield, then `adapter.fields()`; `adapter.create` (an S3 kind must pass its probe; its keys are sealed) |
| owner search | `vault:manage_people` (GET, JSON) | any ACTIVE account by name, username or e-mail; answers pk, name, username — never the e-mail |
| Edit | `vault:manage_edit` (POST) | name, owner, quota, AI shield only; any other posted field refuses the whole post (`bucket_lifecycle.update_bucket`, `VAULT.BUCKET.UPDATED` before/after) |
| Test | `vault:manage_test` (POST, JSON) | `adapter.probe`, stamped (a mount's on its pairing) |
| Delete | `vault:manage_delete` (POST) | the name typed exactly, checked again server-side; `bucket_lifecycle.request_deletion` hands the purge to a worker |

A refusal is Post/Redirect/Get: the session's `vault.manage_draft` re-opens
the modal with what was typed and each sentence beside its field — never a
secret field, never the access key id (`NEVER_CARRIED`), and error sentences
are scrubbed of what was typed into them.

The two-sided flow with another Zenobia plugs in through three partials:
`vault/manage/_connect_button.html` (header), `vault/manage/_row_share.html`
(per bucket) and `vault/manage/_extra_modals.html` (its modals), which all
read one switch, the page's `share_connect_config` (2026-10-01: a button shows
exactly when its modal is on the page; the old `remote_buckets_enabled` tag is
gone) — see the next section.

Tests: `tests_management` (doors × visitors, the list in table and cards,
Create / Edit / Delete / Test), `tests_remote_page` (the tab bar, remote rows),
`tests_share_connect` (the two-sided flow).

## Another Zenobia: share and connect (`share_views.py`, 2026-09-30)

Two Zenobias link one bucket with a **pairing code**: base64 over JSON
(`peering.pairing_code_for` — the grant's id, its magic token, a fresh api key,
the bucket's slug, the rights, and since 2026-09-30 the end date and the
sharing Zenobia's address). It is made on the Zenobia that HOLDS the files and
entered on the one that wants to USE them. Both sides live in Storage →
Management, Superuser plan only (every door: `plan_gate.superuser_plan_door`,
JSON 403 before any lookup), and neither exists where
`VAULT_EXTERNAL_BUCKETS = False`.

**Sharing side** — a bucket's **Share** (a local or S3 bucket; never one
connected from a third Zenobia — `BucketGrant.clean`'s no-daisy-chain rule —
nor one being deleted):

1. The modal explains what sharing does and lists the bucket's shares
   ("Shared with": label, rights, until when, made when and by whom, last
   use, status — never the grant's id, token, key or key hint;
   `vault:manage_shares`, JSON).
2. **New share** (`vault:manage_share`, POST): who it is for (hosts from
   `peering.federated_host_choices()` suggested), the rights as four
   checkboxes — ALL OFF until ticked, at least one required, and List with
   any of them (connecting reads the share's manifest, which the peer answers
   only with List; the form ticks it for you, the server refuses a share
   without it, and the connecting side refuses such a code with that reason),
   a warning under Delete — how long (7 days by default; 1 / 30 / 90 days, a
   year, or no end date), and this Zenobia's address as the other one
   reaches it (the request's own by default).
3. The answer is the ONE response that ever carries the code: an HTML
   fragment (`vault/manage/_share_code.html`, `Cache-Control: no-store`) with
   the code, Copy, the QR code, numbered steps for the other administrator and
   "will not be shown again". The grant stores only a hash of the key; the
   code is not in a column, the session, a message, JSON, a log or the audit
   chain, and closing the modal removes it from the page.
4. **Rotate key** (`vault:manage_share_rotate`): a new key (and optionally a
   new end date — "keep" by default while the share runs); the old key stops at
   once; the new code and QR are shown the same way, once. **Revoke**
   (`vault:manage_share_revoke`, confirmed): `is_active = False` at once; the
   row stays for the record and is never revived.

**Connecting side** — **Connect a bucket from another Zenobia**, a stepper
whose doors are all JSON and take the code in the POST body from the
browser's memory (the code field has no `name`; no door ever answers with the
code or any part of it; error sentences are scrubbed; `sensitive_post_parameters`
keeps it out of error reports; nothing goes to the session draft):

1. What the other administrator does first; the code **pasted**, **scanned**
   with the camera, or read from a chosen **image** of its QR code.
2. `vault:manage_connect_preview`: what it grants — the other Zenobia, its
   bucket, the rights, the end date — before anything is saved. A malformed,
   expired or already-connected code is refused with what to do. The address
   this server calls: the code's, a federated host, or typed.
3. `vault:manage_connect_test`: the connection test (the peer manifest over
   the SSRF guard, `outbound.assert_outbound_allowed`), its answer shown;
   nothing saved.
4. A local name and an owner (people search) → `vault:manage_connect`: the
   test runs again and must pass, then `ZenobiaRemoteAdapter.create` makes the
   `BucketPeer` (key sealed under FIELD_ENCRYPTION_KEY — a permanent one is
   required) and the bucket in one transaction.

A code already connected here whose key was **rotated** on the other side is
offered **Replace the stored key** (`vault:manage_connect_renew`): the new key
is tried against the stored address first, and only then replaces the sealed
one — the pairing, its buckets and their files stay.

Audit: `VAULT.BUCKET.SHARED`, `SHARE_ROTATED`, `SHARE_REVOKED`,
`PEER_KEY_REPLACED` (label, rights, end date — never a key, token or code); a
connected bucket is `VAULT.BUCKET.CREATED`.

### The QR path

The QR code is drawn **in the browser**, from exactly the code string, by the
vendored qrcodejs (`vendor/qrcodejs/qrcode.min.js` — the welcome page's
`oya/partials/_local_qr.html` uses it too); it can be saved as a PNG for the
image path. It is never sent to a QR service. Reading it is also local:
`BarcodeDetector` where the browser has one, else the vendored jsQR
(`vendor/jsqr/jsQR.js`, Apache-2.0, fetched by the host's `download_vendor.py`
like every vendor asset and loaded only when that fallback is needed). The
camera is asked for only after **Scan QR**, and stopped as soon as a code is
read, on Stop, when the modal closes or the page is hidden; a chosen image is
decoded in the page and never uploaded. A refused camera, a missing one, an
insecure page and an image without a code each say so.

**Security note: whoever holds the QR code holds the grant.** A photo of the
screen, a screenshot or the saved PNG is the same live credential as the text
— it lets its holder use the bucket with the ticked rights until the share
ends or is revoked. Hand either over privately; revoke (or rotate) a share
whose code or picture went somewhere it should not have.

## Outbound safety

`outbound.py` guards the two places this host fetches an operator-supplied URL:
`BucketPeer.base_url` (via `PeerClient.__init__`) and the S3 `endpoint_url` (via
`_build_client`). One function, both sinks. It refuses userinfo/backslash URLs
that make `urlsplit` and `requests` disagree about the host, refuses any address
that resolves private/loopback/link-local/reserved, and refuses plain http —
with `VAULT_OUTBOUND_ALLOWED_HOSTS` as the explicit escape hatch.

**Redirects are never followed.** The guard checks the address it is given,
once; `PeerClient._request` passes `allow_redirects=False` and refuses any 3xx
as a broken peer (stamped, never its `Location` or body), so neither the typed
address nor the other Zenobia can bounce a request — with its api key header —
to an internal service.

An **unresolvable** host is allowed through on purpose: a name that does not
resolve cannot be connected to either, and refusing on DNS failure would redden
every offline test suite. **DNS rebinding is a known residual** — the guard
resolves at check time, and pinning the checked IP for the connection would mean
rewriting `peer_client._http()` and a botocore endpoint resolver.
