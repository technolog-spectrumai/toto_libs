# toto.socialhub

Communities and the people in them: the community directory and pages,
organisation charts, people's profiles, joining by endorsement (apply, prove
you are human, name a referee, wait for them to accept), and what a community
grants its members (`CommunityPrivilege`, see [PRIVILEGES.md](PRIVILEGES.md)).
Membership itself is one relation, `people.Person.communities`, and every app
that asks "who is in this community" reads it.

## Communities and clearances — orthogonal on purpose

Since 2026-09-28 the platform has two kinds of group, and since 2026-09-29
they are two models: a **Community** (`devs`, `testers`) says what somebody
does here; a **Clearance** (`socialhub.Clearance` — `internal`,
`confidential`, `onboarding`) says what they are trusted to read. A
clearance is named after what it **opens**, never after who holds it.

| | Community | Clearance |
|---|---|---|
| Examples | `devs`, `testers` | `internal`, `confidential`, `onboarding` |
| Carries | plan offers, discounts, privileges, news, a page, a forum | who may **read** — through the GROUPS it keeps (wiki topics, vault buckets, map domains; never single items), by `clearance_access` — and how fast its holders' **mana refills** (`regen_security`/`regen_compute`/`regen_storage`, per hour; blank = the pool's own) |
| Membership | `Person.communities` | `Person.clearances` |
| Joined | by application, accepted by a referee | given by a superuser: the Clearances tab, the admin, the console |
| Shown to members | yes — directory, profiles, map, API | never; it has no page |
| Tree (`parent`) | offers inherit down it | none: a clearance stands alone, membership is direct |
| How many | any number | at most **seven** on a platform (`MAX_CLEARANCES`): an eighth is refused on save |

A person usually holds one of each. A senior engineer is in `devs` and
holds `internal`; the CTO is in `devs` and holds `confidential`; a junior
tester is in `testers` and holds `onboarding`. What they may buy and do
comes from the community, what they may read — and how fast their mana
comes back — from the clearance, and neither says anything about the other.

**A design feature.** Each axis is one relation on the person, and every app
that asks "who is in this community" or "who holds this clearance" reads
that relation: nothing is copied into a second membership table, no parallel
ACL has to be kept in step, and admitting or removing somebody is one change
the whole platform sees at once.

**A cybersecurity feature: defence in depth.** A mistake or a compromise on
the money axis — a wrong offer, a discount, a plan, a privilege — cannot open
a page, and a mistake on the reading axis — giving somebody the wrong
clearance — cannot buy anything or grant a right. The two are separate
tables, so no query over communities can ever return a clearance and no
plan, discount, privilege or application can name one: the type enforces
what a flag would have had to police at every use site. What remains is
where each axis is READ: the resolvers (`_offered_keys`, `best_discount`,
`offering_communities`, `privileges.has_privilege`) read `Person.communities`
only, and `clearance_access` and `toto.mana.services.clearance_speeds` read
`Person.clearances` only. Joining a community — the one self-service door on
the platform — can therefore never grant access to anything.

**Clearances go on groups, never on items** (2026-09-30). Each app keeps
GROUPS to clearances and the rule reaches the items through them: the wiki's
**topics** (a page is read through its topics — zenobia's `toto.wiki`), the
vault's **buckets** (a file through its bucket — sheets and decks included),
and locations' **map domains** (routes, map layers, places, addresses, zones
and territories through the domains they are in). One rule,
`clearance_access.group_gate` / `group_hidden`: an item in no kept group
follows its app's own rule; an item in kept groups is read by superusers and
by whoever holds, for EVERY kept group it is in, one of that group's
clearances — **pessimistic** — and by nobody else: not its owner or creator,
not the public flag, not an ACL. A clearance both keeps and grants, and a
hidden item answers as a missing one. Each group model carries a
`(group, clearance)` table with a PROTECT on the clearance, so the Clearances
list counts the groups each keeps and refuses deleting one still in use.
Where a group's clearances are set is the group's own page, superusers only:
the wiki's Topics page and `/wiki/clearances/` grid, a bucket's page in the
vault, the Locations → Domains tab.

### Where clearances are hidden

A clearance has no page and appears in no directory, profile, chip, map
filter, API answer or connector: only the Clearances tab, the admin and the
console list them, and all three are superusers' alone. A member learns the
names of the clearances they hold on the Mana page's Regeneration tab, and
never of the others.

### Who gives clearances

Superusers, on the **Clearances** tab of this app (`communities/clearances/`,
beside Profiles and Communities in the strip, shown to superusers only). The
list (2026-09-30) is read-only: a table on a wide screen, a card per
clearance on a narrow one, five a page (the shared server pagination), each
with its holders, its mana refill speeds, how many things it keeps, and a
**Delete** that is refused while an app still keeps something to it (their
through tables PROTECT the clearance). A **Table | Graph** switch (the
vault's tree/grid idiom, remembered per browser) shows the same clearances as
a Cytoscape graph: each clearance, the groups it keeps (found by their PROTECT
on the clearance, never named), and optionally its holders
(`communities/clearances/graph/`, JSON, superusers only). **New clearance** opens a modal: the
name, a speed per pool, and holders found by searching people
(`communities/clearances/people/?q=`, JSON, superusers only), made all or
nothing; a refusal comes back to the list with the modal open, what was typed
kept and the reason inside it (Post/Redirect/Get, through the session). At most
seven clearances.

Holders and speeds change afterwards in the Django admin, on the clearance's
own page (its **Members** field, written through `clearance.members.set`, the
relation's own door), or from the console (`community_members join ada
--clearance internal`). What a clearance KEEPS is set on each group's own
page (topics, buckets, map domains) — and, when the clearance is made, in the
New clearance modal's **What it keeps** section: pick a kind (wiki topics,
buckets, map domains), search, add. The kinds are plugins
(`plugins/clearance_plugins.py`, `ClearanceTargetPlugin`, autodiscovered from
each app's `plugins/clearance_plugins.py`), so the socialhub imports none of
those apps; each adds the clearance through its app's own door, which writes
the app's own audit record, inside the same transaction as the clearance. Never the membership
application: it names communities, and a community grants no reading.

## My account

Since 2026-09-30 a signed-in member has one page for their own account,
**My account**, reached from the top bar (`oya/header.html`) and mounted by the
host at `/account/` (`account_urls.py`, namespace `account`; views in
`views/account.py`). It lives here because what it edits first is the member's
`Person`, which this app already shows; `toto.core` keeps the sign-in helpers
the later sections call. Its own URL module so the address stays short and
does not move with the socialhub's prefix. Free on every plan (`account` is in
the subscription gate's `ALWAYS_FREE`).

Each section is its own form posting to its own door, own account only by
construction — no view takes a person, a slug or a user id, and the page never
creates or deletes an account (console only; a first save creates the
`Person` row, as the map pin does):

- **Profile** (`/account/profile/`, `AccountProfileForm`): display name, about
  you, avatar and phone — nothing that decides access rides along. The avatar
  goes through the platform's upload rules without entering the vault: Pillow
  must read it as JPEG, PNG, GIF or WebP, at most 2 MB
  (`SOCIALHUB_AVATAR_MAX_BYTES`) and 4096 pixels a side, the host's
  `VAULT_REFUSED_FILE_TYPES` apply, and the antivirus door
  (`toto.vault.scanning.scan`) is asked — today it answers "not scanned" for a
  raster image. It is stored under a random name with the extension of what is
  inside, never the member's filename, and the picture it replaces or clears
  is deleted from storage.
- **Time zone** (`/account/timezone/`): `Person.timezone`, an IANA name
  validated against `zoneinfo` (blank = the platform's `TIME_ZONE`).
  `toto.core.middleware.ProfileTimezoneMiddleware`, placed after
  `ProfileLanguageMiddleware`, activates it for each request and deactivates
  it after, so every page shows times in the member's zone; a name this
  Python no longer knows falls back to the default.
- **Password** (`/account/password/`, 2026-09-30): Django's
  `PasswordChangeForm` — the current password, then the new one twice through
  `AUTH_PASSWORD_VALIDATORS`. This session stays signed in
  (`update_session_auth_hash`, which also gives it a new key) and every other
  session of the member is ended, desktop tokens included
  (`toto.core.user_sessions.end_other_sessions`; a token that escaped the sweep
  is still refused by its session-hash check, `toto.api.tokens`). Recorded as
  `AUTH.PASSWORD_CHANGED` with the number of sessions ended, and the member is
  mailed a "your password was changed" notice through
  `toto.core.notices.send_notice` — synchronous and fail-safe; a failed mail
  never fails the change. An account that signs in elsewhere (no usable
  password) is told so instead of shown the form.
- **E-mail address** (`/account/email/`, 2026-09-30; `email_change.py`):
  the member types a new address and nothing about the account changes yet.
  A random token is mailed to the NEW address (`send_notice(...,
  "email_change_confirm", to=new)`) and only its SHA-256 is kept, on a
  `PendingEmailChange` row — one per member, a new request replacing the old.
  The link (`/account/email/confirm/?token=…`, a query parameter so the
  chain's recorded path never carries it) moves the account only when opened
  within 24 hours by that same member, signed in; opened by another member it
  changes nothing and stays usable. It works once (the row is deleted as it is
  used). `User.email` changes, and `Person.email` with it when the Person held
  the old address or none. The old address gets an "e-mail changed" notice.
  An address is refused when another account, another Person or any
  membership application holds it, compared without case, at the request and
  again at the click — `social_login` matches accounts by `User.email` and an
  accepted application activates the account with its address.
  `AUTH.EMAIL_CHANGE_REQUESTED` and `AUTH.EMAIL_CHANGED` carry the addresses
  masked (`j***@example.org`). A federated account (no usable password) is
  told to change it at its provider, which rewrites it at each sign-in.
- **Sessions** (`/account/sessions/…`, 2026-09-30): where the member is signed
  in now — browsers and desktop/API tokens — from `toto.core.models.UserSession`,
  a row per sign-in written on `user_logged_in` (Django's session table has no
  user column) and removed on `user_logged_out` or when ended here. Each shows
  its kind, user agent, address (`toto.core.client_ip`), first sign-in and
  last seen (refreshed at most every five minutes, `UserSessionMiddleware` for
  cookies and `toto.api.tokens` for tokens); the one in use is marked "this
  device". The row stores the session key itself — deleting a session needs
  it, and it already sits in `django_session` in the same database — but the
  page, the log and the chain name a session only by the row's id. **End**
  (`sessions/<id>/end/`, POST) ends one of the member's own sessions, another
  member's id is a 404; **Sign out everywhere else** (`sessions/end-others/`)
  ends every one but this. Ending deletes the session from the store, so a
  desktop token made from it is refused from its next request. Rows whose
  session expired or vanished drop out of the list (and are deleted) when it
  is drawn. `AUTH.SESSION_ENDED` (kind, row id) and
  `AUTH.SIGNED_OUT_EVERYWHERE` (`sessions_ended`) go on the chain. A sign-in
  from a (user agent, address) pair not seen for this member in 90 days mails
  a "new sign-in" notice (`toto.core.user_sessions`, pairs kept as hashes in
  `KnownSignIn`); an account's first sign-in with nothing known is the
  baseline and mails nothing.
- **Recent sign-ins** (2026-09-30): the member's own `AUTH.*` records of the
  last 30 days — sign-ins and sign-outs, failed attempts typed with their
  username or e-mail (any case), sign-in pauses naming them, password changes
  and resets, e-mail changes asked for and made, ended sessions, changes to the account's flags — each with its
  time (in their time zone), what, address and browser. Read through
  `toto.audit.queries.member_auth_records`; the audit pages stay staff-only.
  A change made by another account (a staff member) shows without that
  person's address or browser. Paged on its own `?signins_page=` — the
  Sessions list has `?page=`; `oya/partials/_server_pagination.html` takes a
  `page_param` for that — and each list's link carries the other's page.
- **Key store** (`/account/key-store/`, 2026-10-01; `toto.gervazy.personal`):
  the member creates their own personal key store — a gervazy strongbox
  named `strongbox` (the name the desktop app's `GET /vault/api/strongbox/`
  already gives it) with its master key and first data key, made by
  `GervazyCryptoSession.initialize_strongbox` like every other box — under a
  passphrase typed twice, at least 6 characters (the storage PIN's rule).
  Before this the web could only initialise a box the admin, the seed or the
  desktop had made. It never overwrites: a box of that name, keyed or bare,
  refuses (a bare one is initialised on the keys page, `/gervazy/my-keys/`,
  which keeps its salt), and so does a second request racing the first (the
  owner+name unique constraint). It also refuses — rolled back — when the new
  box would become the member's `user_strongboxes.first()` while another box
  already is: that box's salt is what their sealed vault files open with
  (`vault/storage_pin.py`). There is no recovery code, so nothing is shown
  once: the passphrase is the only way in, goes to the key derivation and
  nowhere else (`sensitive_post_parameters`, never the session, a message or
  a log), and cannot be recovered. `AUTH.KEY_STORE_CREATED` carries the
  box's id only; gervazy's `CryptoAuditLog` gets a
  `create_personal_strongbox` row naming the box. The section shows the
  form, "exists but has no keys yet", or "ready", with a link to My keys.

Every profile or time-zone change is a `SOCIALHUB.PROFILE_CHANGED` record
naming the fields (`fields`), never their values; the password, e-mail,
session and key store changes are `AUTH.*` records (`toto.audit.identity`),
which is why Recent sign-ins shows them too.

Every string the page, its forms and its messages show is marked for
translation (`{% translate %}`, `{% blocktranslate %}`, `gettext`); the
Polish catalogue is filled in separately.

## On the audit chain

Since 2026-09-28 every community and clearance operation is a record on the
platform's audit chain (`audit.py`, signals — so the admin, the membership
flow, the wiki's Clearances page and a shell are all covered): a community or a
clearance made, changed (the fields, before and after) or removed; a person
added to or taken out of a community (`MEMBER_ADDED`/`_REMOVED`) or given or
losing a clearance (`CLEARANCE_MEMBER_ADDED`/`_REMOVED`), from either side of
the relation and through a `clear()`; senior members; privileges; an application submitted and
each of its steps; a reference asked for, given (the applicant admitted) or
declined; a member's own profile or time zone changed on My account
(`PROFILE_CHANGED`, the field names only). Communities and clearances are recorded apart — the chain is where
a crossing of the two axes would show. Sign-ins, sign-outs and
accounts are recorded by `toto.audit.identity`. See `toto/audit/README.md`.

