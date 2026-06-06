# Bento

Bento is a small **idea-graph** app: a personal knowledge base of *boxes* (nodes)
connected by *links* (edges). Each box captures an idea, note, or quote; links
describe how ideas relate (`about`, `supports`, `contradicts`, `expands`, …).
The whole thing can be browsed as a list or explored as an interactive graph.

The design centre is deliberately minimal: **a node is a `label` plus a `properties`
bag**, and **an edge is a `label` plus a `properties` bag**. Everything that used to
be a dedicated column (body, source, quote, …) now lives inside `properties` as JSON.

---

## 1. How to use it

### As a user

The app lives under `/bento/` and the top nav has five tabs: **Boxes**,
**Relations**, **New box**, **New link**, **Categories**.

- **Create a box** — *New box*. Give it a short **label**, optionally pick a
  **category**, and fill in the **Metadata (JSON)** editor. Well-known keys the UI
  understands are:

  ```json
  {
    "body": "The main idea / description",
    "source_title": "Made to Stick",
    "source_url": "https://…",
    "source_type": "book",
    "quote": "An optional quote",
    "rating": 5
  }
  ```

  Any extra keys you add (e.g. `rating`, `status`, `tags`) are kept verbatim and
  shown in the box's **Properties** panel.

- **Link boxes** — *New link*, or use **Link from this / Link to this** on a box's
  detail page. A link has a **from box**, a free-text **label**, a **to box**, and its
  own **properties** JSON (e.g. `{"strength": 0.9}`).

- **Browse & search** — the **Boxes** page has full-text search (matches the label and
  the `body` / `source_title` / `source_type` / `quote` keys inside `properties`),
  summary stats, and a **Graph view** button that opens a Cytoscape map of all boxes,
  categories, and their edges.

- **Browse relations** — the **Relations** page lists every link as a thin
  `label · from → to` row (each box clickable to its detail), with a search over the
  relation label and the linked boxes' labels.

- **Categories** — lightweight groupings (name + slug + description) managed under
  **Categories**.

- **Lock a note** — privileged users (see *Locking* below) can password-lock a box.
  The body is encrypted at rest and hidden until someone unlocks it.

### Seeding sample data

```bash
# from the repo's portal/ directory
../venv/bin/python manage.py ingress_bento --full
```

This creates sample categories, idea boxes, and links.

### Running / testing

```bash
cd portal/
../venv/bin/python manage.py migrate
../venv/bin/python manage.py runserver        # browse http://127.0.0.1:8000/bento/
../venv/bin/python manage.py test toto.bento  # run the test suite
```

> Tests must be run from `portal/` — running from the repo root causes a
> `toto.toto.X` double-import model conflict.

---

## 2. Technical design

App module: `toto.bento` (`app_name = "bento"`). Three models, all inheriting
`toto.core.domain.DomainEntity` (which adds a universal `uid` UUID for cross-system
identity / graph projection).

### Models — [models.py](models.py)

**`IdeaBox`** — the node:

| Field | Type | Notes |
|---|---|---|
| `label` | `CharField` | The node title (the only first-class content field). |
| `category` | `FK → Category` | Optional, `SET_NULL`. A real relation, not a property. |
| `properties` | `JSONField` | **All node data**: `body`, `source_*`, `quote`, plus any custom keys. |
| `is_locked`, `lock_salt`, `encrypted_body`, `lock_nonce` | lock state | Operational columns for the encryption feature. |
| `created_at`, `updated_at` | timestamps | |

The well-known property keys are re-exposed as **read-only `@property` accessors**
(`body`, `source_title`, `source_url`, `source_type`, `quote`) so views and
templates can keep saying `box.body` / `box.source_title` while the storage is a
single JSON column. `get_property(key, default)` / `set_property(key, value)` are
the generic accessors.

**`IdeaLink`** — the edge: `from_box`, `to_box` (FKs, `CASCADE`), a free-text
`label`, and a `properties` JSONField. `unique_together = (from_box, to_box, label)`.

**`Category`** — `name`, auto-`slug`, `description`.

### Why `label` + `properties`

The node/edge were collapsed to a generic graph shape: the only structural fields are
the label, the relations (`category`, `from_box`/`to_box`), and operational columns
(lock state, timestamps, `uid`). Everything semantic is JSON. This keeps the schema
stable as the set of "fields" evolves — you add a key, not a migration — and mirrors
the property-graph model (label + property bag) used elsewhere in the platform.

### Querying JSON

Because `body`, `source_title`, etc. are JSON keys, search uses Django's JSONField
lookups rather than column lookups:

```python
IdeaBox.objects.filter(properties__body__icontains="memory")          # body search
IdeaBox.objects.filter(properties__source_title__icontains="stick")   # source search
```

### Locking — [views.py](views.py) · `box_lock` / `box_unlock`

Locking is gated to **federal agents** (`request.user.community_profile.is_federal_agent`,
from `toto.people`). `IdeaBox.lock(password)` derives a key with **Argon2id**
(`lock_salt`) and **AES-GCM**-encrypts `properties['body']` into `encrypted_body` /
`lock_nonce`, then blanks the plaintext body and sets `is_locked`. `unlock(password)`
reverses it. Serializers null out the body while a box is locked.

### Forms — [forms.py](forms.py)

- `IdeaBoxForm` → `label`, `category`, `properties` (the properties widget is a
  textarea upgraded to the ACE JSON editor).
- `IdeaLinkForm` → `from_box`, `label`, `to_box`, `properties`.
- `CategoryForm` → `name`, `slug`, `description`.

### Routes — [urls.py](urls.py)

**HTML pages**

| Name | Path | View |
|---|---|---|
| `box_list` | `/bento/` | box list + search + graph modal |
| `link_list` | `/bento/relations/` | relations list (thin `label · from → to` rows) |
| `box_create` / `box_update` / `box_delete` | `/bento/new/`, `/bento/<pk>/edit/`, `/bento/<pk>/delete/` | box CRUD |
| `box_detail` | `/bento/<pk>/` | detail page |
| `box_lock` / `box_unlock` | `/bento/<pk>/lock/`, `/unlock/` | POST, federal-agent only |
| `link_create` / `link_delete` | `/bento/links/new/`, `/links/<pk>/delete/` | edge CRUD |
| `category_list` / `category_create` / `category_update` | `/bento/categories/…` | category CRUD |

**JSON endpoints** ([api_views.py](api_views.py))

| Name | Path | Purpose |
|---|---|---|
| `api_box_list` | `/bento/api/boxes/` | `GET` list, `POST` create (`label` + `properties` + `category_id`) |
| `api_box_detail` | `/bento/api/boxes/<pk>/` | `GET` / `PATCH` / `DELETE` |
| `api_box_links` / `api_link_list` | `/bento/api/boxes/<pk>/links/`, `/api/links/` | list / create links |
| `api_link_detail` | `/bento/api/links/<pk>/` | `DELETE` |
| `api_category_list` | `/bento/api/categories/` | list categories |
| `api_full_graph` | `/bento/api/graph/` | all nodes + edges for the Cytoscape modal |
| `api_boxes` / `api_box_graph` | `/bento/api/boxes-graph/`, `/api/boxes/<pk>/graph/` | HTML-page support feeds |

The JSON API is the contract consumed by the **Enigma** Tauri app. It returns
`label` + a `properties` object (body nulled when locked) — **not** top-level
`title`/`body` (this changed in the label+properties redesign, so Enigma's bento
screens need updating to match).

### Migrations

`0001_initial` defines the `label` + `properties` shape directly. (The schema was
reset during the redesign rather than data-migrated, so an existing DB needs a fresh
`migrate` and re-seed.)

---

## 3. User-interface design

Bento's templates extend `oya/base.html` and use the platform's **Tailwind +
Alpine.js** styling with a dark/light theme (`darkMode`) and FontAwesome icons.
Base shell: [templates/bento/base.html](templates/bento/base.html) — a "Toto Studio /
Bento" header, a Dashboard back-link, and the five-item nav.

- **Box list** ([box_list.html](templates/bento/box_list.html)) — left sidebar with
  search and two stat tiles (boxes / links). The main column is a card per box showing
  label, locked/source badges, a body excerpt, and the source title. A **Graph view**
  button opens a full-screen modal rendering the whole graph with **Cytoscape**
  (`cose` layout): boxes are rounded rectangles, categories are plain ellipses; link
  edges are solid arrows, category-membership edges are dashed. Clicking a node
  navigates to its detail page; colors track the active theme.

- **Relations list** ([link_list.html](templates/bento/link_list.html)) — mirrors the
  box list shell (sidebar search + stat tiles) but the main column is one thin row per
  link: a relation-label chip, then `from → to` with an arrow icon, each box clickable
  to its detail page.

- **Box detail** ([box_detail.html](templates/bento/box_detail.html)) — title and
  timestamps, the rendered **body** (or a locked placeholder),
  and side-by-side **Source** and **Quote** panels, plus a **Properties** panel that
  shows the JSON in a **read-only ACE viewer** (syntax-highlit, theme-aware, cursor
  hidden, auto-sized). A right sidebar lists outgoing/incoming links (with quick
  "Link from/to this" actions) and a small live API-graph summary. Delete, lock, and
  unlock are Alpine-driven modals; lock/unlock post to the API via `fetch` and reload.

- **ACE JSON editor / viewer** — `properties` is always presented through ACE, vendored
  at `toto/toto/core/static/vendor/ace/`, with the init script
  [_metadata_ace_scripts.html](templates/bento/_metadata_ace_scripts.html) handling two
  modes:
  - **Editable** ([_metadata_ace.html](templates/bento/_metadata_ace.html)) — used in the
    box/link forms. The real form control is hidden and kept in sync with the editor,
    giving a live **Valid/Invalid JSON** status chip and a submit guard that blocks
    invalid JSON.
  - **Read-only** ([_metadata_ace_view.html](templates/bento/_metadata_ace_view.html)) —
    used on the detail page. Seeded from a Django `json_script` element and locked with
    `setReadOnly(true)`.

- **Forms** ([box_form.html](templates/bento/box_form.html),
  [link_form.html](templates/bento/link_form.html)) — a generic field loop where the
  `properties` field renders the editable ACE editor above.

- **Confirm/delete & category** pages are simple themed forms following the same
  card-and-modal conventions.
