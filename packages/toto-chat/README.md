# toto-chat

`toto-chat` is the forum of the **toto** suite: a community chat with one channel per community. It ships a single Django app, `toto.forum` (label `forum`, URL namespace `forum`), and depends on `toto-base` only. Since 2026-10-07 it is the simplified forum: no rooms, no room passwords, no direct messages, no WebSocket. The forum it replaced (rooms over WebSockets, search, exports, voice notes) is in git history; the last commit with it is `aaa37a41`.

## What it does

- **One channel per community.** `ForumChannel.community` is a one-to-one to `socialhub.Community`, so the database refuses a second channel. A channel has no name, slug, password or member list of its own: its address is the community's slug and whoever belongs to the community may read it.
- **Messages, images and polls.** A message is text, an image (JPEG, PNG, GIF or WebP, told by its bytes, at most 10 MB), or both. A poll has two to ten options, answers that may be changed or are final, and a count shown as it grows or when it closes. A message is never edited; it may be removed, which wipes its content and leaves a tombstone.
- **Sealed at rest.** Message text, poll questions, option labels and texts and image bytes are AES-256-GCM ciphertext under one key per channel; the keys are wrapped under a strongbox that the platform secret `FORUM_VAULT_PASSWORD` opens. `src/toto/forum/SECURITY.md` says what is sealed, what is not, and how the secret is kept.
- **Images through the vault.** Each channel has one bucket in `toto.vault`; an image is stored there sealed, and read only through the forum's image door.
- **Plain HTTP.** A page asks the feed door for what changed since a cursor. Nothing is held open.

## Access (`access.py`)

Signed in; the `forum` entitlement on the member's plan (never Free); a member, senior member or head of the community, or an administrator (a superuser on the Superuser plan). The head and administrators moderate; staff alone is nobody. Every route's view carries a `forum_door` mark and `tests/test_access.py` walks the URLconf.

## Addresses

Mounted by the host at `/forum/`; `<slug>` is the community's.

| Method | URL | What |
|--------|-----|------|
| GET | `` | the member's communities, one channel each |
| GET | `<slug>/` | the channel's page (its data in a `json_script` block; `static/forum/channel.js`) |
| GET | `<slug>/feed/` | `?after=<seq>` what changed; `?before=<number>` older history; `?limit=` |
| POST | `<slug>/post/` | form fields `op`, `text`; a file `image` |
| POST | `<slug>/messages/<uuid>/remove/` | its author, the head, an administrator |
| GET | `<slug>/messages/<uuid>/image/` | the image, opened, with its checked type |
| POST | `<slug>/polls/open/` | JSON `title`, `options`, `closes_at`, `revisability`, `visibility` |
| POST | `<slug>/polls/<uuid>/vote/` | JSON `choice` |
| POST | `<slug>/polls/<uuid>/close/`, `…/remove/` | who opened it, the head, an administrator |

## Modules

`models.py` (the tables), `channels.py` (the one channel, its bucket, the event counter), `keys.py` (channel keys), `sealing.py` (the seal), `access.py` (who may), `posting.py` (posting, removing, the feed), `images.py` (images and the vault), `voting.py` (polls), `billing.py` (the seam where a post is charged), `erasure.py` (an erased member), `views.py` and `urls.py` (the doors), `management/commands/ingress_forum.py` (a channel for every community).

## Wire it into a host

```python
INSTALLED_APPS += ["toto.forum"]           # after toto.socialhub, toto.vault, toto.gervazy
FORUM_VAULT_PASSWORD = os.environ.get("FORUM_VAULT_PASSWORD", "")   # minted once, kept for good
path("forum/", include("toto.forum.urls")),
```

Then `manage.py migrate` and `manage.py ingress_forum`. No channel layer, no ASGI route, no attachment directory.

**Tests.** Name the modules (`toto` is a namespace package):

```bash
python manage.py test toto.forum.tests.test_access toto.forum.tests.test_encryption \
  toto.forum.tests.test_posting toto.forum.tests.test_images toto.forum.tests.test_polls \
  toto.forum.tests.test_pages
```

## Build & packaging

toto-chat is one of the 16 lockstep-versioned wheels in the toto suite. All siblings share a single `VERSION` (currently **2.0**) and pin each other exactly; this package depends on **`toto-base==2.0`**. Versions are rewritten only by the repo's release tooling (`scripts/release.py`) — never edit them by hand — and `scripts/check_package_graph.py` enforces that each wheel owns a disjoint slice of the `toto.*` namespace. Packaging is standard setuptools (`src/` layout, namespace packages, with `templates/`, `static/`, and `graph/*.yaml` bundled as package data). Hosts pin the packages they install, all at one version, in `requirements.toto.txt`.

For the full build, versioning, and release manual, see the repository root README.
