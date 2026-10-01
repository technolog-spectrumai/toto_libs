# toto.polls — parked 2026-08-28

Community consultations and the quiz desk. `Question` + `Choice` + `Ballot` ran
the polls; `quiz_models.py` / `quiz_services.py` / `quiz_views.py` /
`render_pdf.py` ran quizzes — take one, get a scored attempt, download an
immutable `QuizCertificate` as a PDF. It also carried the `polls.pdf` metric and
the `downloads.metered_pdf` billing sequence that certificate used.

## Why it was parked

A product decision, not a fault. Polls became **part of the Forum**: a poll now
belongs to a room by a real ForeignKey (`forum.RoomPoll`), is opened from the
room's Polls tab, and is answered by the room's members. This app existed to be
the engine behind that tab — it even moved host → wheel in 8/2026 *so that the
forum could import it* — and once the forum owned its own polls there was
nothing left for the engine to do.

The quiz desk travelled with it because it lives inside this app, and because
learning features belong to the reusable Delta application rather than here.
**Quizzes were not rebuilt in the forum, deliberately.**

Parked in the LIBRARY limbo (`vendor/toto_libs/limbo/`) rather than a host's,
because this is wheel code: it shipped inside `toto-base`. Only zenobia ever
installed it — faros, delta and placidia did not, and delta has its own
quizzes app — so nothing else notices.

## Its tables are deliberately left in place

`polls_question`, `polls_choice`, `polls_ballot`, the six `polls_quiz*` tables
and `polls_pollsusageevent` / `polls_pollsquotapolicy` all stay. Django simply
stops managing them, `django_migrations` keeps its rows, and un-parking
reconnects to the existing data. The two quota tables matter most: they hold
every host's real `polls.pdf` limits and usage history, and the metric's code
was never renamed precisely so those limits would survive.

If you *want* the old consultations and votes gone — the poll data specifically,
not the quizzes — use `zenobia/scripts/drop_departed_tables.py`, which is a dry
run by default and needs `--i-have-a-dump-at`. Drop only `polls_ballot`,
`polls_choice`, `polls_question`, child before parent, and **leave the quiz and
quota tables alone**.

## What breaks while it is parked

* **`/polls/` is gone**, and with it the quiz desk: `polls:quiz_list`,
  `quiz_take`, `quiz_result`, `quiz_statistics` and `quiz_certificate`. Anyone
  holding a certificate keeps the row — `QuizCertificate` is immutable and its
  `delete()` always raises — but **the page that renders it as a PDF is not
  mounted**, so an issued certificate cannot currently be downloaded again.
  That is the one consequence worth knowing before reviving or replacing this.
* The `polls.pdf` metric leaves the registry, so it stops appearing on the rate
  desk. Nothing is charged for it either way; its rows are untouched.
* The `polls` subscription entitlement was removed from the catalogue and from
  both plans in the same commit — see `ingress_subscriptions.py`, which records
  why: the gate reads the entitlement off the URL namespace, `forum` is free,
  and room polls already resolved as `forum`.
* Nothing else. No app FKs into `polls`; `toto.governance`'s `governance.E001`
  check forbids importing it and keeps passing trivially.

## Reviving it

1. `git mv vendor/toto_libs/limbo/polls vendor/toto_libs/packages/toto-base/src/toto/polls`
2. Add `"toto.polls"` back to the host's `INSTALLED_APPS` and, if it should seed,
   to `INGRESS_ALLOWED_APPS`.
3. Re-add the `path("polls/", include("toto.polls.urls", namespace="polls"))`
   mount, and the dashboard tile **plus its `DASHBOARD_CATEGORIES` entry** — a
   tile in no category is invisible to everybody with no error at all.
4. Re-add its three test modules to `zenobia/scripts/clean_env_test.sh`; an
   unnamed module runs nowhere and the suite still reports green.
5. Bump the app count in `vendor/toto_libs/tests/test_packaging.py` back up.
6. `manage.py migrate` — the tables are still there and reconnect as they are.

**If you revive it for the quizzes only**, note that the polls half now
duplicates `forum.RoomPoll`. Two poll engines on one host is not a state anybody
wants; take the quiz modules out into their own app first, or take Delta's.

## The 2026-10-01 migrations reset

Every library and host migration was then reset to a fresh initial and every
database rebuilt, so the tables above are gone from rebuilt databases and step
6 of a revival creates them instead. Its migrations name a pre-reset node
(`people` 0004); a revival regenerates them against the fresh graph.
