# toto-repo

`toto-repo` is the version-control distribution of the toto suite. It ships two
Django apps that solve two halves of one problem and are deliberately not the
same app:

- **`toto.repo`** — a real git repository over a vault directory, entirely on
  this host's disk. Init, commit, branch, checkout, merge (with a per-file
  conflict resolver), a commit-graph history view, restore-to-a-commit, and
  push/pull to any git URL.
- **`toto.gitea`** — the optional remote: per-user accounts on a co-deployed
  Gitea, a repository picker, and the credentials `toto.repo` needs to push
  there.

A host installs one, the other, or both. It ships as one of the lockstep-versioned
wheels that share the `toto.*` PEP 420 namespace and is pinned by host projects
in `requirements.toto.txt`.

## Why two apps

They were one app, `toto.gitvault`, behind one flag. Splitting them followed
from the hosts actually diverging:

- **zenobia** is the platform master. It runs a Gitea sidecar behind the
  portal's own single sign-on, and — since documents, decks and sheets got their
  own plain version history in `toto.vault` — has nothing left that wants a
  worktree. It installs `toto.gitea` alone.
- **placidia** is a federation consumer (`TOTO_AUTH_MODE=consumer`). It mounts
  no OIDC provider, so it **structurally cannot run Gitea**: the sidecar is a
  relying party of a provider that is not there, and `deploy.py validate`
  refuses the combination. But it owns the workspace labs, which are exactly the
  thing worth versioning. It installs `toto.repo` alone.

One flag could not express that, and one app could not be half-installed.

## `toto.repo`

### What it does

A `GitRepo` turns one vault directory into a git repository. The worktree on
disk is a **materialized mirror** of the vault subtree — the vault database
stays the source of truth between operations — so every mutating flow is the
same three steps under one lock:

```
repo_lock → export the vault subtree → run git → import what git changed back
```

Encrypted vault files are never exported: they are listed as skipped instead, so
a repository is never quietly missing content it appears to cover.

### Models

- **`GitRepo`** — `OneToOne` to `vault.VaultDirectory`, an owner, a default
  branch, and `remote_url`: any git URL, stored verbatim. Nesting a repository
  under another repository's subtree is refused at init.
- **`GitRepoFile`** — the vault-file ⇄ worktree-path correspondence, owned
  exclusively by the sync engine. It is what lets import distinguish a rename
  from a delete-plus-create.
- **`GitRun`** — one background `init` / `push` / `pull`, with an optional FK
  into `workflows.WorkflowRun` so the operation also shows up in the workflows
  UI. This FK is why the distribution depends on `toto-flow` and why
  `BUILD_REPO` forces `BUILD_WORKFLOWS` in `toto.features`.
- **`RepoUsageEvent` / `RepoQuotaPolicy`** — the metering pair every metered app
  declares for itself (`toto.quota` owns no tables).

### Execution paths

`dispatch.py` picks one of three, in order: a `WorkflowRun` wrapping the
`repo_run` predefined task when celery is up and the `repo-run` workflow has
been seeded by `ingress_repo`; a bare `run_git_task` when celery is up but the
workflow row is missing; inline execution when there is no worker at all. The
`GitRun` row is the UI's source of truth on all three.

### Metering

Two metrics, because the work has two shapes and pricing them together would
misprice both:

| code | what it counts | default |
|---|---|---|
| `repo.run` | one init/push/pull — a worktree export plus a network git | 100 |
| `repo.op` | one git subprocess inside the request: commit, branch, merge, log | 300 |

The read-only doors (`history`, `commit_detail`) are metered too, deliberately:
they take the repo lock and fork git like everything else, and being cheapest to
call makes them the most attractive to hammer.

### Access

Every endpoint and every toolbar sits behind `REPO_ACCESS`, three levels in the
`EXECUTION_ACCESS` shape: `"staff"` (the default), `"superuser"`,
`"authenticated"`. Read lazily, so `override_settings` can exercise each.

### Remotes, and how Gitea reaches them

`toto.repo` pushes to whatever URL it holds, with no stored credentials — a
private remote surfaces git's own auth error, which is honest. When something
*can* supply credentials for a URL, it registers a provider:

```python
# toto/repo/remotes.py
registry.register(RemoteProvider(name=..., claims=..., credentials=...))
```

`toto.gitea` registers exactly one, claiming the URLs of the co-deployed Gitea
and minting a per-user token for them. With no provider installed the lookup
returns an empty pair, which is precisely what a custom remote already got. On a
host that installs only one of the two apps this seam is inert by construction.

## `toto.gitea`

### What it does

Auto-provisions a Gitea identity per portal user and keeps it usable:

- Ensures the Gitea user exists, matching the OIDC identity (username + email)
  so Gitea's `ACCOUNT_LINKING=auto` links their SSO login to it.
- Mints a `portal-repo` access token through Gitea's admin API, running as the
  `portal-svc` admin account that `deploy/gitea/provision_oauth.sh` creates
  alongside the OIDC source.
- Stores that token Fernet-encrypted under `FIELD_ENCRYPTION_KEY`. The raw value
  never lands in the database or in `.git/config`: push and pull inject it per
  invocation via an `http.extraHeader`.
- Creates repositories with the **user's** token, so ownership and attribution
  in Gitea are theirs and not the service account's.

### Models

- **`GiteaAccount`** — `OneToOne` to `auth.User`, a Gitea username, an encrypted
  token. That FK is its only one, which is what lets the app stand alone on a
  host with no local repositories at all.

### Pages

`gitea:index` lists the user's repositories with links into Gitea, and provisions
the account on first visit. `gitea:repos` is the JSON picker `toto.repo`'s init
modal reads when both apps are installed.

### Settings

| setting | meaning |
|---|---|
| `GITEA_ENABLED` | `services.gitea` in the deploy config sets this; everything is inert without it |
| `GITEA_URL` | same-origin path nginx proxies Gitea at (default `/gitea/`) |
| `GITEA_INTERNAL_URL` | REST/clone base on the compose network (default `http://gitea:3000`) |
| `GITEA_SVC_PASSWORD` | the `portal-svc` admin password |
| `GITEA_ACCESS` | who may use it, same three levels as `REPO_ACCESS` |

`GITEA_ENABLED` off is a supported state, not a broken one: the page says so and
offers nothing.

## Flags and installation

```
BUILD_REPO=1     → toto.repo   (forces BUILD_WORKFLOWS — the GitRun FK)
BUILD_GITEA=1    → toto.gitea  (refused on TOTO_AUTH_MODE=consumer)
```

`toto.features` resolves both; `registry.FEATURE_APPS` maps them to their app
labels. `ingress_repo` seeds the `repo-run` workflow and belongs in
`INGRESS_ALLOWED_APPS` after `toto.workflows`.

## History

`toto.gitvault` was a zenobia host app while zenobia was the only host with
surfaces worth versioning. It moved into `toto-flow` in 1.50 when the workspace
labs went to placidia and their versioning had to follow — a host app cannot do
that. It split here, into `toto-repo`, when the two halves turned out to belong
to different hosts.

The split renamed the app label, so the `gitvault_*` tables are dropped rather
than migrated (`zenobia/scripts/drop_departed_tables.py`). That was affordable
because they held no repositories, no runs and no accounts — only two seeded
quota-policy rows.
