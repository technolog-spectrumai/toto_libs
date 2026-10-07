# Forum security

What protects a community's channel, in transit and at rest, and how its
keys are kept. Written 2026-10-07 with the simplified forum (one channel per
community). The forum before it (rooms, room passwords, temporary rooms) is
in git history, with its own version of this file.

## 1. In transit

TLS, terminated by the host's nginx. Every door is plain HTTP over it: a
page asks the feed door for what changed; there is no WebSocket and no held
request. The doors are session doors: Django's CSRF check applies, and a
write that another site sent is refused by Fetch Metadata
(`toto.api.fetch_metadata`).

## 2. Who gets in

`access.py`, asked by every door (`views.door`; each route carries a
`forum_door` mark and a test walks the URLconf):

- signed in;
- the `forum` entitlement on the member's plan (never Free). The plan gate's
  middleware asks the same of the URL namespace; the forum asks again;
- a member, a senior member or the head of the community, or an
  administrator (a real superuser on the Superuser plan). Staff alone is
  nobody. Leaving the community closes the channel at the next request:
  there is no member list of the channel's own;
- to remove another member's message, or close or remove another member's
  poll: the community's head, or an administrator;
- to open the Settings page (`/forum/settings/`), save its dials or start a
  cleanup (the mark `administrator`): an administrator of the platform and
  nobody else. Not staff alone, not a superuser without the Superuser plan,
  and not a community's head, who moderates one channel and has no dial.

There are no private channels, no channel passwords, no direct messages and
no second channel: `ForumChannel.community` is a one-to-one, so the database
refuses one.

## 3. At rest

Sealed with AES-256-GCM (`sealing.py`, through `toto.gervazy.crypto`), a
fresh 96-bit nonce per seal, the associated data binding each frame to its
channel and its row, so a frame copied onto another row or into another
channel fails authentication instead of opening:

| What | Where | Sealed |
|---|---|---|
| a message's text | `forum_forummessage.body_sealed` | yes |
| a poll's question | `forum_channelpoll.title_sealed` | yes |
| an option's label and text | `forum_pollchoice.sealed` | yes |
| an image's bytes | a `vault.VaultFile` in the channel's bucket | yes |
| who sent a row, and when | the row | no |
| the sender's display name on a row | `sender_name`, `opener_name` | no |
| an image's type and size | `attachment_mime`, `attachment_size` | no |
| the ballots (who chose which option) | `forum_pollballot` | no |

There is no plaintext column for any sealed thing, so nothing can be stored
in clear by mistake or by fallback. Removing a message or a poll wipes its
sealed content at once; an image's bytes leave the vault with it.

This is server-side encryption at rest, not end-to-end: the server opens a
channel's key to serve its members. It protects a database dump, a backup
and a disk from whoever holds them without the secret; it does not protect
against the running server or its operators. The platform has no
per-person encryption keys, no key directory and no browser key store, so
end-to-end would need all of those first.

## 4. Keys

    FORUM_VAULT_PASSWORD ─Argon2id▶ UKEK ▶ VMK ▶ DEK   (the `forum-channels` strongbox, gervazy)
                                                 └─AES-GCM▶ channel key ─AES-GCM▶ content

- **One 32-byte key per channel**, made once by `keys.ensure_key` when the
  channel is made, and stored only wrapped (`forum_forumchannelkey`).
- **One platform secret**, `FORUM_VAULT_PASSWORD`, opens the strongbox the
  channel keys are wrapped under. There is no channel password and no key
  derived from one.
- **Minted once, kept for good.** The host's `deploy.py` mints the secret on
  the first deploy and carries the existing value forward on every later
  one; it is never on a command line and lives only in the server's
  git-ignored env file.
- **Restarts.** A process keeps opened keys in memory only. After a restart
  the wrapped rows are read again with the secret; nothing else is needed.
- **Backups.** The database dump holds the wrapped keys and the sealed rows;
  the media volume holds the sealed images. **Neither can be read without
  `FORUM_VAULT_PASSWORD`**, which is in neither: keep the server's env file
  with the backups, somewhere else than the backups themselves. A restore
  needs all three, and the secret must be the one the data was sealed under.
- **Losing the secret loses every message, poll and image for good.** There
  is no escrow and no recovery.
- **Without the secret, or with another one**, every door that reads or
  writes content answers 503 with a sentence, a new channel is not made,
  and nothing is stored.
- **Rotation.** The secret can be changed without touching any content
  (`keys.vault.rotate_passphrase(old, new)` re-wraps the strongbox's master
  key only), then the env file is updated. A channel key itself is not
  rotated: `ForumChannelKey.version` is there for the day it is.

## 5. Images

Told by their first bytes to be a JPEG, PNG, GIF or WebP (`images.sniff`),
never by the sender's word or the file's name; anything else is refused, so
a page, an SVG or an Office file is never stored. At most 10 MB. Stored
sealed through the vault in the channel's own bucket (no owner; the vault's
own doors can give the file's owner or a superuser the ciphertext only).
Read through one door, which asks `access.may_read` every time and answers
with the checked type, `X-Content-Type-Options: nosniff` and
`Cache-Control: private, no-store`. The forum owns exactly the files its
message rows point at; any other file in that bucket is not the forum's.

## 6. On the page

Names, messages, questions and options leave the server as JSON strings, or
inside a `json_script` block, and are written by `static/forum/channel.js`
as text nodes. Nothing a member wrote is put into a page as markup, and an
address in a message is not made a link.

## 7. Mana and cleanup

A post is charged per kilobyte (`billing.py`), in the transaction that
stores it: a refused or failed post is never charged, and a retry of the
same press never charged twice (the `op`, then a usage event keyed by the
message). A usage event and a ledger entry hold the metric, the message's id
and a number of bytes; nothing of the message. The estimate door stores and
charges nothing.

A cleanup (`cleanup.py`) removes for good everything of a channel made
before a boundary: messages with their images (the vault's row and bytes),
polls with their options and answers, tombstones. No copy is kept. It reaches
a vault file only through a message row that points at it and never lists a
bucket, so any other file kept in a channel's bucket is not touched. Only an
administrator starts one by hand, by a POST with a ticked confirmation; the
nightly one runs only while an administrator has switched retention on. The
boundary is worked out on the server when the cleanup is claimed, and the
removing happens on the worker, in a workflow node that finishes only the
records its dispatcher claimed and that the Workflows API refuses to start
by hand. Every cleanup leaves a `ForumCleanupRun` (who, when, the boundary,
the counts) that the admin shows read-only and nobody can delete. The forum
writes no record on the platform's audit chain, for a cleanup or anything
else.
