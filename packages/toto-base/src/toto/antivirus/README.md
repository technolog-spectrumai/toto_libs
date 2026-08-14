# toto.antivirus

Content screening for the vault. Files are checked at every door on the way
in, and anything you hold a claim on can be scanned on demand. **A scan never
changes a file** — every scanner refuses, none rewrites: a rewritten file is
one the author never wrote, and for a document somebody is editing that is a
worse outcome than a refusal they can act on.

Ships in `toto-base`; installed only where `BUILD_ANTIVIRUS=1` (zenobia
today). Everything the rest of the platform touches goes through the façade
`toto/vault/scanning.py`, which degrades to inert answers on hosts without the
app — so nothing else ever imports this package directly.

## The two kinds of scanning

**Door screening (inline, free).** Every write path — editor save, upload,
gateway, websocket, restore — calls `scanning.scan()` before bytes land. A
refusal means the save simply does not happen; the file on disk is untouched.
Inline because the refusal decides the save; free because it is the platform
protecting itself, not a service somebody ordered. Which of YOUR OWN file
types are auto-screened is a per-user `ScanPreference`; the preference can
only narrow your own files, never anybody else's.

**On-demand scans (queued, billed).** The Scan button in the app creates a
`ScanRun`, dispatches it through the workflow engine onto celery, and the
browser polls — the same `dispatch / runner / predefined_tasks / sweeps`
pattern texlab, aralia and steven use, including the named refusal when no
worker is listening and the stuck-run sweeper for SIGKILLed workers. One
metric (`antivirus.scan`, priced in `ingress_tariffs`), checked
(quota + funds) **before** dispatch, charged **after** a delivered verdict.
A run that fails — an unreadable file — charges nothing and needs no refund.

## Verdicts are about bytes, not files

`ScanResult` is keyed on `(file, content_sha256)`. Editing a file does not
make its old verdict wrong — it makes it about something else. That is why:

- the clean tick and the Files-tab icons disappear the moment content changes;
- Pathology marks every finding **"Still present"** or **"Bytes changed
  since — historic"**, so a fixed file stops reading as infected;
- re-scanning unchanged bytes updates one row instead of piling up rows.

Three verdicts: `clean`, `refused`, and `error` — the scan itself could not
run (unreadable bytes). Recorded rather than swallowed, because a file that
*cannot* be checked is a security fact of its own kind. Severity on the
Pathology tab is **derived** from the refusal reason (`severity_of`): the
active-content family is a *Threat*, structural refusals are *Suspicious*.

## The scanners

A scanner is `(text) -> Verdict` — or `(bytes) -> Verdict` when registered
with `binary=True` — living in `scanners/`, registered in `scanners/__init__`.
Registration is pure data (imported from `ready()`, so no DB, no settings).
Another app adds one by shipping `<app>/scanners.py`.

| type | rules, in one line |
|---|---|
| `svg` | no active content, no doctype/entities, references only to itself or its own embedded images |
| `html` | no scripts, handlers or script-URLs; ordinary links allowed; the **bare HTML5 doctype is allowed** (it is inert — refusing it banned every real HTML file), anything more is not |
| `xml` | as html, but any doctype refuses — XML parsers process DTDs, which is where XXE lives |
| `json` | must parse, bounded nesting depth |
| `pdf` | binary; refuses the active-content name tokens (`/JavaScript`, `/JS`, `/OpenAction`, `/AA`, `/Launch`, `/EmbeddedFile`, `/RichMedia`, `/XFA`) and encrypted documents it cannot inspect. Parses nothing — a parser is attack surface. Deliberately does **not** refuse `/AcroForm` or signature machinery: notarius writes signed PDFs, and a scanner that refuses the platform's own output is a ban, not a guard |

`SCANNABLE_TYPES` lives in the façade (`toto/vault/scanning.py`) because the
vault needs the list without importing this app.

## The app's three tabs

- **Files** — the vault's tree, read-only, in security terms. Green check =
  scanned clean; red warning = unscanned / failed / threat (tooltip names
  which); greyed rows cannot be scanned and say why. The Scan button opens a
  confirmation modal (the same tree, checkboxes on scannable rows) and the
  modal shows progress as the queued scans run one by one.
- **Statistics** — files scanned, scans run, data scanned (from `size_bytes`
  recorded at scan time), verdict counts, and a 30-day stacked activity chart.
- **Pathology** — every finding with severity, door, detail, line and the
  currency badge. Own files for users; staff see the platform.

The vault carries exactly one antivirus surface: the shield button above the
file tree, linking here. Bucket metrics show a health card
(`scanning.health_report`) — absent, not zeroed, on hosts without the app.

## Traps worth knowing

- `ScanResult.Meta.ordering` puts `-scanned_at` into any
  `.values().annotate()` GROUP BY — end aggregates with `.order_by(...)`.
- The scannable set is `views._my_files` and the endpoints accept exactly it;
  the tree may LIST more (disabled rows), but every enabled control must
  resolve to that queryset. Tests enforce this.
- Templates: the shared chart partial needs `oya/partials/dim.html` and an
  explicitly-sized wrapper, or the canvas collapses.

## Usage

**As a user.** Open **Antivirus** from the dashboard (or the shield button
above the File Vault tree). The Files tab shows everything you can read;
press **Scan** (or **Re-scan**) on anything with a claim of yours — the modal
asks you to confirm exactly what will be scanned, then shows progress as the
scans run. Statistics is your aggregate picture; Pathology is every finding
with enough detail to investigate. Under *Scan automatically* you choose which
of your own file types are screened at the doors as they are saved.

**As an operator.** Staff see platform-wide Statistics and Pathology. Quota
and price live where every metric's do: the limit under Metered
(`antivirus.scan`), the price on the rate card (seeded by `ingress_tariffs`).
On-demand scans need a worker (`BUILD_WORKFLOWS=1` + celery); without one the
button refuses by name. Door screening works with no worker at all.

**As a developer.** Never import this app — call the façade:

```python
from toto.vault import scanning

verdict = scanning.scan(content, file_type="html")   # Verdict(ok, scanned, …)
scanning.record(vault_file, verdict, door="mydoor")  # remember it
scanning.clean_file_ids(files)                       # the green ticks
scanning.health_report(files)                        # bucket card, or None
```

Every function degrades to an inert answer when the app is absent, so callers
carry no `if installed` branches. Adding a scanner: one module in `scanners/`
plus one `register("type", fn)` — or `<yourapp>/scanners.py`, autodiscovered.

## Tech stack

- **Django** models/views/templates; no DRF — JSON endpoints are plain
  `JsonResponse`.
- **Alpine.js** for the Files tab state (modal, checked set, polling); no
  build step, no bundler.
- **Chart.js** (vendored, never CDN) through `oya/partials/chart.html` for the
  Statistics activity chart.
- **celery via toto.workflows** for on-demand scans: a one-node workflow and a
  `@register` predefined task, autodiscovered — no `TASK_MODULES` entry.
- **toto.quota / toto.tariffs** for metering and money: the standard
  `AbstractUsageEvent`/`AbstractQuotaPolicy` pair, `check_quota` +
  `check_funds` before dispatch, `record_usage` + `charge` after a verdict.
- **Scanners are stdlib only** — `html.parser`, `re`, `json`, `hashlib`. No
  lxml, no pypdf: parsing hostile input with a big parser is attack surface,
  and the whole design avoids it.

## TODOs

- [ ] **Zip archives**: `zip` is a vault type; scanning members (bounded
      depth, bounded total size, refuse on zip-bombs) is the obvious next
      scanner and the hardest to get right.
- [ ] **Images**: no scanner — a text scanner cannot honestly judge pixels.
      Refusing polyglots (a JPEG that is also a valid ZIP/HTML) is feasible
      and worth a look.
- [ ] **Re-scan from Pathology**: a "Still present" finding should offer the
      re-scan without a trip back to Files.
- [ ] **Batch progress persistence**: the modal's progress is client-state;
      leaving the page abandons the view (the runs finish server-side).
- [ ] **PDF marker review**: `/AA` and `/XFA` are refused conservatively;
      if legitimate interactive forms turn up in real use, the list needs a
      policy decision rather than a quiet edit.
- [ ] **Polish translations** for the new tab strings (the catalogue is
      pending platform-wide).
