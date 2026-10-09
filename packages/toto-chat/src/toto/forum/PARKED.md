# toto.forum — parked 2026-10-09

The owner, 2026-10-09: "ok, forum sucks, lets get rid of it. park forum. plan
the parking move first. with all apendages."

Parked on zenobia since that day (stage 72 of that host's tracker), for the
second time. The first parking ran from 2026-10-04 (stage 57) to 2026-10-07:
it took the old forum (rooms over WebSockets, room passwords, direct
messages) off the host and left every hook in the library where it was,
dormant behind "is the forum installed". What came back on 2026-10-07
(stages 68 to 70) is the simplified forum in this folder; it grew poll
threads on 2026-10-08. The last commit with the old forum is `aaa37a41`.

This parking goes further than the first, and it is not the move the apps in
`limbo/` made:

- **The folder did not move.** The code and its tests are here as they stood
  on 2026-10-09, inside the `toto-chat` wheel, which zenobia does not pin.
  Outside this folder only toto-business's `toto.company` still asks whether
  the forum is installed (its `CompanyForum`, left on purpose; see below).
- **The hooks are gone.** Everything other library apps carried for the forum
  was removed, one commit for each (the list below). Nothing of the forum
  runs, is offered or is promised, whatever a host installs.
- **No flag installs it.** `BUILD_CHAT` is not read, the realtime tier
  defaults nothing of it, and `toto.registry.FEATURE_APPS` has no `chat`.

## What it was

- **One channel per community.** `ForumChannel.community` is a one-to-one. A
  channel has no name, slug, password or member list of its own: its address
  is the community's slug, and who belongs to the community may read it.
- **Threads headed by a poll** (2026-10-08). A `ChannelPoll` is a thread: a
  question with two to ten options and one answer for each member, who may
  take it back and answer again while the poll is open; the count is shown
  as it grows or only when the poll closes. A `ForumMessage` is one reply in
  a thread: text, an image (JPEG, PNG, GIF or WebP, told by its bytes, at
  most 10 MB) or both. A reply is never edited; removing one wipes its
  content and leaves a tombstone. A closed poll can be archived, and each
  poll action leaves a sealed snapshot of its result (`ForumPollAudit`) that
  outlives the poll.
- **Sealed at rest.** Reply text, poll questions, option labels and image
  bytes are AES-256-GCM ciphertext under one key for each channel. The keys
  are kept wrapped under the `forum-channels` strongbox (gervazy), which the
  platform secret `FORUM_VAULT_PASSWORD` opens. `SECURITY.md` says what is
  sealed and what is not.
- **Images through the vault.** An image is a sealed `vault.VaultFile` owned
  by the member who posted it, in the thread's own bucket, which has no
  owner.
- **Plain HTTP.** A page asks the feed door what changed since a cursor, at
  short intervals. Nothing is held open: no WebSocket, no channel layer.
- **Who gets in.** Signed in; the `forum` entitlement on the member's plan,
  never Free; a member, a senior member or the head of the community, or an
  administrator. The head and administrators moderate (`access.py`).
- **Mana.** A post drew on storage mana for each kilobyte of its text
  (`forum.text_kb`) and of its image (`forum.image_kb`), in the transaction
  that stored it (`billing.py`).
- **Cleanup.** Closed and archived threads with no activity since a boundary
  were removed, replies, images and ballots with them: by a nightly task
  while an administrator had retention switched on, or by an administrator
  by hand from the Settings page. Either way it was a run of the "Forum
  cleanup" workflow on the worker, and it left a `ForumCleanupRun`
  (`cleanup.py`, `dispatch.py`).

## Do not install it as it stands

**Do not add `toto.forum` to a host's `INSTALLED_APPS` without first bringing
back the hooks listed below, above all the erase hook and the data-copy hook
(`bbe3356c`). The app would start, migrate and serve its pages without any of
them, and nothing at start-up would say that something is missing.**

What would be wrong, silently:

- **An erased member's messages would be kept under their name.**
  `erase_user` no longer calls this folder's `erasure.py`, so the display
  name copied onto each of their replies and polls would stay, and so would
  every text, still signed with it. The erase dialog and the erase report
  would say nothing about a forum.
- **A member's copy of their data would leave their messages out.**
  *Download my data* no longer has the table of the member's own messages.
- **It would be open on every plan.** The plan catalogue no longer declares
  `forum`, and a key the catalogue does not know is free
  (`subscriptions.gate.is_entitled`): `access.entitled` would let a member on
  Free in.
- **A post would cost nothing.** No colour and no seed price means no row on
  the rate card, and with no price a post is free (`billing.py`).
- **Nothing would run the nightly cleanup**, retention switched on or not,
  and Monit's Jobs page would list no cleanup.
- **No page would lead to it.** No panel on a community's page, no chapter in
  the manual.

The forum's own tests are a tripwire for part of this, and they are left as
they were for that reason: `tests/test_pages.py` (the community panel, the
data copy), `tests/test_cleanup.py` and `tests/test_settings.py` (the beat
entry, the worker's list) and `tests/test_billing.py` (the prices) assert
hooks that are gone, so they fail on a host that installs the app without
them.

## The hooks that were removed

Each was removed from the library by one commit on `dev_platform`; reverting
a commit brings its hook back. A host that vendors the library carries the
same change as a commit of its own with the same one-line message. Paths are
under `packages/`.

- **The community page and the profile's sentences** — `ec338df3`
  ("Socialhub: the forum panel and sentences go").
  `toto-base/src/toto/socialhub/plugins/community_plugins.py`:
  `CommunityForumPlugin`, the panel that led from a community's page into
  its channel, drawn only for who may read it; its template
  `socialhub/community_plugins/forum.html`. In `socialhub/_profile_data.html`
  the forum's branches of the Your data tab: "forum messages" in what the
  copy holds, and in the erase dialog the pictures among what goes and the
  message texts among what stays. `socialhub/tests_forum_room.py` and
  `socialhub/tests_erasure_request.py` assert the absence now.
- **The erase and the data copy** — `bbe3356c` ("Core: no forum in erase or
  data copy"). `toto-base/src/toto/core/erasure.py`: the report's two counts
  (`sent_by`), `forget_sender` before the account goes and `delete_blobs`
  after the commit, all three in this folder's `erasure.py`.
  `core/personal_data.py`: `_forum()`, the table of the member's own
  messages with their text opened. The forum's lines in the docstring of
  `core/management/commands/erase_user.py`, and `ForumTests` in
  `core/tests_erase_leftovers.py`.
- **The manual** — `e5e401e3` ("Manual: the Polls and Chat chapters go").
  The keys `polls` and `chat` of `_manual_feature_map` in
  `toto-base/src/toto/core/views.py`, and the Polls and Chat chapters with
  their contents lines in `core/templates/oya/manual/_body_en.html` and
  `_body_pl.html`. Both chapters linked `forum:channel_list`.
- **The plan catalogue** — `2c52920d` ("Plans: the forum entitlement and its
  line go"). `Entitlement("forum", "Forum", order=28)` in
  `toto-base/src/toto/subscriptions/catalogue.py` and `forum` under
  Professional in the library's own `subscriptions/plans.yaml`, together: a
  plan naming a key the catalogue does not declare stops the build
  (`subscriptions.E001`).
- **The schedule and the worker's list** — `738a9a88` ("Schedules and tasks:
  no forum cleanup"). `forum_cleanup`, `forum_cleanup_hour` and
  `forum_cleanup_minute` of `toto.schedules.beat_schedule` with the
  `forum-cleanup` entry they made (04:40), and `"toto.forum"` in
  `toto.registry.TASK_MODULES`, without which the worker cannot find
  `tasks.py`.
- **The switch** — `ca256ae6` ("Features: the chat switch goes").
  `Features.chat` and the reading of `BUILD_CHAT` in
  `toto-base/src/toto/features.py` (the realtime tier defaulted it, and it
  was one of three reasons for `needs_channels`), `"chat"` in
  `toto.registry.FEATURE_APPS`, and `"chat"` in the `/api/apps/` descriptor
  (`api/auth_views.py`). The library's `tests/test_features.py` and
  `api/tests/test_auth_views.py` assert the absence now.
- **Mana** — `70424475` ("Mana: the forum's two prices go"). `forum.text_kb`
  and `forum.image_kb` in `COLOUR_OF` (storage) and in `PRICES` (0.001 and
  0.002 a kilobyte) of `toto-economy/src/toto/mana/colours.py`. They are not
  named in `NOT_MANA` either, on purpose: a forum installed without its
  colours fails mana's audit of registered metrics.
- **Monit** — `9c740c11` ("Monit: the forum cleanup job source goes"). The
  `forum_cleanup` source of the Jobs page, over `forum.ForumCleanupRun`, in
  `toto-ops/src/toto/monit/jobs.py`.
- **Two test expectations** — `37d3abbd` ("Tests: no forum dialog, no
  cleanup node"). `forum/channel.html` in
  `toto-base/src/toto/core/tests_focus_trap.py` (the dialog a poll was
  opened in wears the focus trap) and `toto.forum` in
  `toto-flow/src/toto/workflows/tests_dispatch_only.py` (the cleanup node
  may be started only by its dispatcher).
- **Geography's link** — `01673c7f` ("Geography: the forum link goes"). The
  text column `forum_thread` on community pins and zones, removed from
  `toto-geo/src/toto/geography/migrations/0001_initial.py` in place;
  `Contribution.discussion()` in `geography/models.py`; its line in
  `geography/templates/geography/_details.html` and its entry in the
  context of `geography/locations.py`; `geography/tests_forum_link.py`.

Before these, `fd5eacad` ("Forum: poll threads, from the vendored copy")
levelled this folder with the host's vendored copy, where the poll threads
had been written.

## What was left in place on purpose

- The general helpers the forum prompted and other apps may use:
  `toto.quota.charge.quote` and `check_funds_all`, the tariffs pair
  `quote_user` and `check_user_can_act_all`, and mana's branch for very small
  amounts.
- `CompanyForum` in toto-business's `toto.company`, which names a forum room
  by its slug. zenobia does not install that app.
- The Polish of the forum's strings, in the host's catalogue.
- Everything in this folder, tests included.

## What stays in a database that ran it

zenobia rebuilds every database, so nothing below is left there. On a
database that is kept:

- **The forum's tables, with their rows**: `forum_forumchannel`,
  `forum_forumchannelkey`, `forum_forummessage`, `forum_channelpoll`,
  `forum_pollchoice`, `forum_pollballot`, `forum_forumpollaudit`,
  `forum_forumsettings`, `forum_forumcleanuprun`, `forum_forumusageevent`,
  `forum_forumquotapolicy`, and the app's row in `django_migrations`.
- **Vault buckets without an owner**, with the sealed images in them: one
  for each channel, named `<community> — forum`, from the forum of
  2026-10-07; and one for each poll thread, named `Forum poll <poll id>`
  (slug `forum-poll-<poll id>`) with an `Images` folder, from the poll
  threads. Storage → Management lists them like any bucket.
- **A workflow row**, `forum-cleanup` ("Forum cleanup"), with its one node
  and the runs of past cleanups. No installed app registers the node's task
  now, so a run of it fails with "Unknown predefined task" and removes
  nothing.
- **The secret and what it opens.** `FORUM_VAULT_PASSWORD` stays in a
  server's env file until the next fresh deploy; the host's deploy mints
  none any more. In the database stay the `forum-channels` strongbox with
  its data key (gervazy) and the inactive account `forum-vault` that owns
  it. Without the secret none of the sealed rows or images can ever be read
  again: there is no escrow.
- **Two rows of the rate card**, the prices of `forum.text_kb` and
  `forum.image_kb` that `ingress_mana` seeded, and in the ledger the charges
  made for posts. The ledger is sealed and stays as written.

The tables keep their foreign keys to accounts, communities, vault files,
folders and buckets and gervazy's data keys, and Django no longer knows the
tables. So nothing detaches or deletes the forum's rows first, and the
database itself refuses to delete what one of them points at: an account
that posted, voted or opened a poll (`erase_user` fails for it), a community
that has a channel, a file or a bucket a row names. A kept database is not
fit to run without the app until the forum's rows are out of the way: take
a dump, then drop the forum's tables, child before parent.

## Bringing it back

1. Revert the ten commits, newest first (`01673c7f`, `37d3abbd`, `9c740c11`,
   `70424475`, `ca256ae6`, `738a9a88`, `2c52920d`, `e5e401e3`, `bbe3356c`,
   `ec338df3`), in the library and in the host's vendored copy.
2. Do not take what comes back on trust. Three things did not fit the forum
   in this folder even before the parking:
   - `ForumTests` in `core/tests_erase_leftovers.py` were written for the
     forum of 2026-10-07. They post without a poll and read a bucket off the
     channel; since the poll threads `posting.post_message` needs a `poll`
     and a channel has no bucket. This folder's `erasure.py` was not changed
     when the threads arrived either. Rewrite the tests and prove the erase
     against threads before any member's data depends on it.
   - `Contribution.discussion()` in geography asked for a channel by its
     slug. The simplified forum's channel has no slug, so the lookup failed
     and the line was never drawn. Reverting `01673c7f` also changes
     geography's first migration again: every database made in between is
     rebuilt.
   - The two manual chapters and the profile's sentences describe the old
     forum (rooms to join, messages that appear live, voice recordings, "the
     room's record"). They want writing again.
3. On the host: `toto.forum` in `INSTALLED_APPS` (after `toto.socialhub`,
   `toto.vault` and `toto.gervazy`) and in the ingress; the `forum/` mount;
   `FORUM_VAULT_PASSWORD`, minted once by the deploy and kept;
   `forum_cleanup=True` in the call to `beat_schedule`; `forum` on a plan of
   the host's own plan file; the Forum tile; the `toto-chat` pin; the
   forum's paragraphs of the privacy notice. zenobia's `technology.md`
   records its side under Parked and retired.
4. Run every module in this folder's `tests/` and the tests the reverts
   bring back, on a host that installs the app. Then `manage.py migrate` and
   `manage.py ingress_forum`.
