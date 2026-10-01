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
  you, avatar and phone, and whether other members see the phone number and
  the e-mail address (two switches, off by default — [Data
  protection](#data-protection-rodo--gdpr)) — nothing that decides access
  rides along. The avatar
  goes through the platform's upload rules without entering the vault: Pillow
  must read it as JPEG, PNG, GIF or WebP (a camera's MPO, a JPEG with a second
  picture behind it, too since 2026-10-01: stored as the plain JPEG in front),
  at most 2 MB (`SOCIALHUB_AVATAR_MAX_BYTES`) and 4096 pixels a side, the host's
  `VAULT_REFUSED_FILE_TYPES` apply, and the antivirus door
  (`toto.vault.scanning.scan`) is asked. The antivirus cannot scan images: it
  answers "not scanned" for every raster picture, avatars included. Since
  2026-10-01 what is stored is the picture drawn again from its pixels
  (`forms.reencode_avatar`, through `forms.redraw_picture`, which a host may
  call with a cap of its own — zenobia's Trix attachments do): turned as its
  EXIF orientation says, in the same
  format, without its EXIF (the GPS position, the camera), XMP, ICC profile or
  comments; an animated GIF or WebP keeps its first frame. A JPEG or WebP is
  encoded at quality 90, or 80 or 70 when that is what fits under the same
  cap; a picture that fits at none is refused. It is stored under a random
  name with the extension of what is inside, never the member's filename, and
  the picture it replaces or clears is deleted from storage. The admin's
  Person form cleans an avatar the same way (`forms.clean_avatar_upload`).
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
  Since the review (2026-10-01): the form asks for the current password (a
  wrong one counts toward the sign-in lockout); asking is limited to 5 times
  an hour per member and 3 links a day per address (`toto.core.ratelimit`);
  the link acts only while the account still has the address it had when
  asked; and a confirmed change ends the member's other sessions.
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
- **Your data** (`/account/data-export/`, 2026-10-01, RODO art. 15 and 20;
  `data_export.py`): *Download my data* queues a copy of everything the
  platform holds about the member — the zip `toto.core.personal_data`
  builds, the same one the console's `export_user` writes — into their own
  personal bucket. A `DataExport` row is written first (status, the vault
  file, counts), then `tasks.build_data_export` runs on a worker; with no
  worker listening the button refuses and writes nothing (never inline). One
  open export per member (a conditional unique constraint), one a day — a
  failed one does not count — and an open row older than six hours is closed
  as failed when the member next looks, so a killed worker never blocks
  them. A redelivered job takes the file its export already filed. The
  section shows the last export's status and links the zip;
  `PRIVACY.EXPORT_REQUESTED` (the member), `_READY` / `_FAILED` (the system:
  counts and the file's id, never contents). **Only the latest zip is kept**
  (the review, 2026-10-01): the zips sat in the bucket outside any quota, one
  a day for ever; now a new one, once ready, deletes each earlier export's
  file for good — the vault's purge (`vault.trash.purge_now`), not the
  trash, where an old copy of personal data would wait a month — live or
  trashed, wherever the member moved it, and marks its row `REPLACED`
  (`data_export.replace_earlier`; the vault's `FILE_PURGED`, door
  `data_export_replaced`). A copy whose bytes will not delete keeps its row
  `READY` and the next export tries again. The section says so before the
  button, and that the copy also holds the audit records others made about
  the member (`toto.core.personal_data`, without their address and browser).
- **Erase my account** (`/account/erasure-request/`, 2026-10-01, RODO art.
  17; `erasure.py`): the member FILES an `ErasureRequest` after a
  confirmation saying an operator carries it out at the console and what is
  kept (the audit chain, the ledger, rows others need, backups). Nothing on
  the web erases: a superuser on the Superuser plan sees the list at
  `/socialhub/privacy/erasure/` (`socialhub:erasure_requests`, linked from
  the notice's editor) with the console command for each open request
  (`SOCIALHUB_ERASURE_COMMAND`, `{username}` shell-quoted; default
  `python manage.py erase_user {username}`) and may only decline one, with a
  note the member reads. toto.core's `erase_user --confirm` marks the
  member's open request done inside its transaction and lists it in
  `requests_closed`. The ticket's user is SET_NULL with a username snapshot,
  so it outlives the account; one open request per member (a conditional
  unique constraint). `PRIVACY.ERASURE_REQUESTED` (the member), `_DECLINED`
  (the superuser; the note's length, not the note), `_DONE` (the system,
  from the console). A declined request shows the member, under the note,
  that they may complain to the President of UODO or go to court, whatever
  the note says (2026-10-01, 37c.21). What the erase takes beyond the
  cascade — the avatar's file, version bodies, the home pin, the
  application and its references (`applications.of_member`), the forum's
  pictures and recordings — and the names it takes off what stays is
  `toto.core.erasure`; the dialog says it, and that the forum texts stay,
  signed "Former member", and backups until they age out. Django's admin
  deletes no account (`toto.core.admin.ConsoleErasedUserAdmin`): it points
  to the console command instead.

Every profile or time-zone change is a `SOCIALHUB.PROFILE_CHANGED` record
naming the fields (`fields`), never their values; the password, e-mail,
session and key store changes are `AUTH.*` records (`toto.audit.identity`),
which is why Recent sign-ins shows them too.

Every string the page, its forms and its messages show is marked for
translation (`{% translate %}`, `{% blocktranslate %}`, `gettext`); the
Polish catalogue is filled in separately. A few short labels carry a
context (`pgettext`, 2026-10-01), because the same English word means
something else on other screens: "Answered" beside an erasure request's
date (`erasure request`), the erasure list's tabs "Open", "Carried out"
and "Declined" (`erasure requests filter`) and "Read" on the notice's
versions list (`privacy notice version`). A host's catalogue needs an
entry with that `msgctxt` for each; without one the label shows in English.

## Data protection (RODO / GDPR)

What the platform does about the personal data it holds, since 2026-10-01 —
most of it here, because the socialhub is where a person first hands the
platform their data, and the rest in `toto.core`:

- **A privacy notice**, versioned, public, edited by a superuser on the
  Superuser plan — [below](#privacy-notice). Its text ships as a clearly
  marked **placeholder** in Polish and English; the real text is the
  organisation's to write and publish — through the editor, or as two files
  the host names (`PRIVACY_NOTICE_TEXTS`), which the ingress publishes.
- **Accepted by new applicants only**, recorded with its version on the
  application and carried to the person as a `PrivacyAcceptance` on
  admission — [below](#privacy-notice). Members from before are not asked.
- **A copy of one's data** (art. 15 and 20): *Download my data* on My
  account queues it into the member's own bucket ([Your data](#my-account));
  the console's `export_user` (`toto.core.personal_data`) writes the same zip
  for anybody else.
- **Erasure** (art. 17) is *filed* on My account and *carried out* only at
  the console, by `toto.core`'s `erase_user` ([Erase my account](#my-account)).
- **Contact details hidden by default** (2026-10-01, 37c.25): other members
  see a member's e-mail address and phone number only when the member
  switched them on (My account, Profile: `Person.show_email`,
  `Person.show_phone`, both off). The member always sees their own and an
  administrator — a superuser on the Superuser plan — keeps seeing both; the
  profile, the roster and the data-mesh org chart all ask
  `contact_access.py`. The org chart answers with what the member shows and
  nothing more, the caller's own included: the desktop client copies it on,
  peer to peer.
- **No visitor's address for other websites** (2026-10-01, 37c.20): the
  Administrata page draws its chain graph with the image's own Cytoscape
  (`vendor/cytoscape/`, as every other graph page), where it fetched it from
  cdnjs.cloudflare.com, and the news editor's Trix comes from the image's
  copy (`vendor/trix/`) through `toto.verbena.widgets.LocalTrixEditorWidget`,
  where django-trix-editor's widget links unpkg.com. A form cannot name that
  widget in `Meta.widgets` — `TrixEditorField.formfield` puts the package's
  back — so the news form and `make_section_form` call `use_local_trix` on
  their built fields.
- **Nothing kept longer than needed**: membership applications that lapse
  are renewed when their applicant applies again, and pruned with the
  never-used accounts they made 30 days after they lapsed
  ([below](#applications-that-lapse)); the nightly housekeeping that prunes
  them also clears expired sessions and the sign-in rows of sessions that are
  gone (`toto.core.housekeeping`).

Not done, and said so: the audit chain keeps usernames and addresses for
good (each record is sealed into the next; reshaping it so they can age out
is a later stage), backups keep whatever they took, and nothing on the web
erases an account.

## Privacy notice

Since 2026-10-01 (RODO / GDPR) the platform has a versioned privacy notice,
`PrivacyNotice` — a version number, the text in Polish and in English, when
and by whom it was published. It lives here rather than in `toto.core`
because it is what an applicant accepts on the membership application, and
the socialhub is where a person first hands the platform their data.

- **Public pages** (`views/privacy.py`): `/socialhub/privacy/` shows the
  current version in the reader's language (Polish for a `pl` reader, English
  otherwise; `?lang=pl|en` shows the other), and `/socialhub/privacy/v<N>/`
  shows any version, kept word for word — what somebody accepted stays
  readable after it is replaced. A host with a site-wide login gate lists
  both routes as public (zenobia: `PUBLIC_ROUTES`). Linked from the footer
  of every page (`oya/base.html`) and from the welcome page.
- **Publishing** (`/socialhub/privacy/edit/`): a superuser on the Superuser
  plan (just a superuser where no such plan is sold) edits both texts,
  pre-filled with the current version, and publishing saves a NEW version —
  no version is ever edited (`privacy.publish`). Both texts are required, at
  most 100,000 characters each (`MAX_TEXT`: a guard against a pasted book,
  50,000 until 2026-10-01, when a real notice came out at about 50,200), and
  an unchanged text is refused; a refusal
  is Post/Redirect/Get with the typed text kept. Every version is listed
  with its date and publisher.
- **Plain text**, drawn escaped through `urlize` and `linebreaks` (the
  wiki's own chain): a blank line starts a paragraph and a web or e-mail
  address becomes a link. Nothing from the database reaches the page as HTML.
- **Seeded**: `ingress_socialhub` publishes version 1 in the realistic and
  full modes when there is none (`privacy.seed_notice`; `seed_placeholder`
  is its old name, kept for hosts that call it). A host that names no text
  gets a clearly marked placeholder in both languages (`PLACEHOLDER — replace
  with your organisation's privacy notice` as the first line), with every
  fact the platform cannot know in [brackets]. The real text is the
  organisation's.
- **A host's own text** (2026-10-01): `PRIVACY_NOTICE_TEXTS = {"pl": path,
  "en": path}` names two UTF-8 plain-text files — the host's, kept with its
  code. The ingress publishes them as version 1 on a fresh database, and as
  the NEXT version where the current one is still the placeholder (its first
  line in either language is the mark), so a platform seeded before its host
  had a text moves off it by itself, through `publish` like any version.
  Once the current version is anything else — the host's text or somebody's
  own — the ingress leaves it alone: a later change to the files reaches a
  running platform through the editor. A file named but missing or
  unreadable is `ImproperlyConfigured`, not a quiet placeholder. What a text
  still leaves its owner to fill in is marked `[[UZUPEŁNIJ: …]]` /
  `[[FILL IN: …]]` (`privacy.markers` lists them by name), and the ingress
  names each one, per language, when it publishes the text — the notice is
  public from that moment. `tests_privacy_seed`.
- **Audited**: `PRIVACY.NOTICE_PUBLISHED`, with the version, the one it
  replaces and each text's length — never the text.
- **Accepted on the membership application** (2026-10-01): the form links
  the current version and requires a tick; the version shown rides along in
  a hidden field, so a version published while the page was open is drawn
  again (box clear) rather than accepted unseen. With no version published
  the application is closed. The application records `privacy_version` and
  `privacy_accepted_at`, and `PRIVACY.NOTICE_ACCEPTED` (the version, the
  application, its e-mail and community) goes on the chain. On admission
  (`ReferenceRequest.save`) the acceptance is carried to the Person as a
  `PrivacyAcceptance` row (person, version, when) — a small table rather
  than fields on Person, because `toto.people` knows nothing of the
  socialhub and a later version accepted is a second row. It cascades with
  the Person on erasure. Members from before acceptance existed have no row
  and are never asked (the owner's choice); nothing gates their sign-in.
  The admin shows the version on applications (read-only) and lists the
  acceptances; the referrer's reference panel on their profile shows
  "Privacy notice vN accepted", linked to that version.

## Applications that lapse

An application is good for a week (`applications.LIFETIME_DAYS`): the code
on the verification page has to be typed within it. One whose week ran out
before its applicant got in has **lapsed** (`applications.py`, 2026-10-01).
Until then it stayed lapsed for ever: the form refused its address as taken
("Membership application with this Email already exists") and the code
answered "This code has expired." — an e-mail address that could never apply
again, and an inactive account nobody would ever use.

- **Applying again renews it.** The form finds the lapsed application by its
  address and checks against that row instead of refusing it
  (`MembershipApplicationForm.clean_email`); the view's `applications.renew`
  gives the SAME row a new code and a new week, the community and the
  privacy notice chosen now, and starts it over — pending, not verified, and
  without the references asked for the lapsed attempt (a pending one would
  otherwise admit the applicant to whichever community they chose this
  time). The account the lapsed attempt made is reused under the username
  typed now — its old name is the applicant's to type again — so the address
  keeps one account and the acceptance (`ReferenceRequest.save`, by address)
  finds it; with none left, one is made as for a new application. An expired
  code now says to apply again with the same address.
  `SOCIALHUB.APPLICATION_RENEWED` and a fresh `PRIVACY.NOTICE_ACCEPTED` go on
  the chain. **So does one whose every reference was declined** (the
  review, 2026-10-01: the decline mail says to apply again, yet the address
  stayed taken for the rest of the week; `applications.declined`).
- **Addresses are compared without case** (the review, 2026-10-01): the form
  refuses `Ann@…` while an application from `ann@…` holds the address, and
  the account an application made — the one its acceptance activates, a
  renewal names again and the reference step sets a password on — is
  `applications.applicant_account`: the accounts at its address in any case,
  one that has not got in first. Before, the first account with the exact
  address was taken, so an address a member also had found the member's —
  activated and enrolled instead, and its password set from that public page.
- **The reference step is the browser's that typed the code** (the review,
  2026-10-01): verifying puts the application's id, with the moment it was
  verified, in that browser's session (`views/application.py`,
  `VERIFIED_SESSION_KEY`), and the step — the referrer, the message, the
  password — and its thank-you page answer only there. Anywhere else they
  answer 403 with a sentence: no form, no address, nothing set, and alike for
  an id that does not exist. Before, anybody holding an application's id (a
  number counted up from 1) could set a pending applicant's password and read
  their address. Typing the code again elsewhere only says "already
  verified", and a renewal clears `verified_at`, so the browser that verified
  a lapsed round no longer counts. An applicant who lost that browser applies
  again once the application has lapsed, as the sentence says.
- **No step's address names the applicant** (2026-10-01, 37c.21): the pages
  were `apply/success/<e-mail>/`, `verify/user/<e-mail>/` and
  `reference/submit/<application number>/`, and a path goes into nginx's
  access log and, as the Referer, to the next site. Now they are
  `apply/success/`, `verify/`, `reference/submit/` and `reference/next/`, and
  each finds its application in the session of the browser that applied
  (`APPLIED_SESSION_KEY`: the id and the end of its week, which a renewal
  moves on). So only that browser is shown the code's picture — before,
  anybody who typed an address into the URL was — and only after it typed
  the code does the reference step open. Any other browser gets a sentence
  and the way to apply (403). No page shows the address typed, and the log
  names an application by its id, never its address, username or code.
- **The nightly housekeeping prunes it** (`applications.prune`, called by
  `toto.core.housekeeping` on the beat) once it lapsed more than
  `SOCIALHUB_EXPIRED_APPLICATION_DAYS` (default 30) days ago — the
  application, its references and the accounts it made, in one transaction.

Either only while the application is nobody's: every account with its
address, in any case, was never active, never signed in, is not staff, and
**owns nothing else** — `erase_user`'s own report (`plan`, Django's deletion
collector) finds nothing to delete, detach or be blocked by beyond the
account and what sign-up gives every account (`SIGNUP_ROWS`: the prepaid
ledger account, which stays detached, and the mana pools' opening fill). A
person, a privacy acceptance, a data export, an erasure request, a file or
a bucket keeps the application and its account, counted as `kept`; an
application whose applicant got in is the member's record and not
housekeeping's at all — known by its accepted reference
(`applications.admitted`), since a member who changed their e-mail has no
account at its address any more. Applications whose account is already gone
(erased) are pruned alone. Counts only reach the chain — one `PRIVACY.HOUSEKEEPING`
record a night (`toto.core.housekeeping`), never an address.

Tests: `tests_application_housekeeping.py` (renewal through the form, the
pruning rules), `toto/core/tests_housekeeping.py` (the night).

## On the audit chain

Since 2026-09-28 every community and clearance operation is a record on the
platform's audit chain (`audit.py`, signals — so the admin, the membership
flow, the wiki's Clearances page and a shell are all covered): a community or a
clearance made, changed (the fields, before and after) or removed; a person
added to or taken out of a community (`MEMBER_ADDED`/`_REMOVED`) or given or
losing a clearance (`CLEARANCE_MEMBER_ADDED`/`_REMOVED`), from either side of
the relation and through a `clear()`; senior members; privileges; an application submitted and
each of its steps, and renewed after it lapsed (`APPLICATION_RENEWED`); a reference asked for, given (the applicant admitted) or
declined; a member's own profile or time zone changed on My account
(`PROFILE_CHANGED`, the field names only); a privacy notice version published
(`PRIVACY.NOTICE_PUBLISHED`) and accepted by an applicant
(`PRIVACY.NOTICE_ACCEPTED`); a copy of a member's data asked for, filed or
failed (`PRIVACY.EXPORT_REQUESTED` / `_READY` / `_FAILED`); an erasure
asked for, declined or carried out at the console
(`PRIVACY.ERASURE_REQUESTED` / `_DECLINED` / `_DONE`); and, written by
`toto.core.housekeeping`, each night's housekeeping, counts only
(`PRIVACY.HOUSEKEEPING`). Communities and clearances are recorded apart — the chain is where
a crossing of the two axes would show. Sign-ins, sign-outs and
accounts are recorded by `toto.audit.identity`. See `toto/audit/README.md`.

