"""What an error mail may carry (2026-10-01).

When a page crashes, Django mails the operators (``ADMINS``) its plain-text
report: the traceback, the request — GET, POST, cookies, headers — and every
setting. That mail leaves the server for somebody's mailbox, so the report is
filtered before it is written, and a host names the classes here in its
settings — the two below, and ``PlatformAdminEmailHandler`` as its
``LOGGING`` handler's class:

    DEFAULT_EXCEPTION_REPORTER_FILTER = "toto.core.error_reports.PlatformExceptionReporterFilter"
    DEFAULT_EXCEPTION_REPORTER = "toto.core.error_reports.PlatformExceptionReporter"

- a setting, header or cookie whose NAME says secret is starred, at any depth
  (``DATABASES``' ``PASSWORD``): Django's own list — API, TOKEN, KEY, SECRET,
  PASS, SIGNATURE — and the platform's: AUTHORIZATION (a bearer token
  header), COOKIE (``CSRF_COOKIE`` in the headers is the CSRF secret),
  CREDENTIAL, PRIVATE, SALT, DSN. ``FIELD_ENCRYPTION_KEY``, ``*_SECRET``,
  ``*_PASSWORD``, ``*_KEY`` and ``*_TOKEN`` are all caught by name;
- a password inside a URL (``redis://:pw@redis``, ``postgres://u:pw@db``) is
  starred wherever a setting or a header carries one, and so is a URL
  parameter whose name says secret (``?token=``, ``?code=``, ``?sig=``);
- EVERY cookie value is starred, whatever its name: the session cookie signs
  in as the member, and no other cookie is a mailbox's business;
- EVERY POSTed value is starred and only the field names are kept. Django
  stars just what a view's ``@sensitive_post_parameters`` names, and a form
  can carry a password under any name — besides, what a member typed into a
  form is theirs;
- a GET parameter is shown unless its name says secret;
- the exception's own message, its causes and the request URL are scrubbed
  of every value starred above, because an exception may echo what it was
  handed;
- a URL PATH that is itself a credential (2026-10-01, the review): every
  value a route in ``SECRET_ROUTES`` captures — the vault's peer routes
  carry a grant's id and its magic token — and, on any route, a value
  captured under a name that says secret (``<str:token>``) is starred
  wherever the mail shows the path: the subject, the line above the report,
  the request URL, ``PATH_INFO`` and the like (``path_secrets``). The
  subject is Django's handler's own, so the host names the handler here too:
  ``PlatformAdminEmailHandler``.

The HTML report is never mailed (the host's handler says ``include_html``
False), so a frame's local variables never leave the server.

``CrashMailFilter`` decides which records are mailed at all: a crash — a
record carrying an exception; a page that answers 503 on purpose because a
service it needs is off is not one — and the same crash (its exception type
along the same lines of code) at most once every ``REPEAT_SECONDS``, counted
in the cache all web workers share. A page that breaks for every visitor
would otherwise send a mail per visit and get the SMTP account the alert mail
depends on suspended; the log keeps every occurrence. When the cache cannot
answer — Redis down, which is exactly when every page that queues crashes —
each process counts for itself (2026-10-01, the review): the first crash is
still mailed, its repeats in that process are not.

**After the commit** (2026-10-01, the review). A crash logged inside a
transaction is mailed once that transaction commits — an SMTP round trip
(up to EMAIL_TIMEOUT) never holds a transaction's locks — and not at all if
it rolls back. Django logs a crashing request after the view's own
transaction has ended, so for a page this is "at once", as before.

**Sent at once, not through the worker** (2026-10-01). The notices and the
alert mail are handed to the Celery worker and tried again for about an hour
(``toto.core.notices``); this mail is not. A crash can be the broker's or the
worker's own — Redis down fails every page that queues — and a report queued
behind them would never leave; a report also carries the request's details,
which have no business waiting in Redis. So Django's handler sends it from
the request that crashed, one try, as it always did: a mail server that
refuses loses that one report (Django sends it ``fail_silently``, so the
Mail check does not count it either), and the log still has the crash.
``CrashMailFilter`` keeps what this costs the request to one send per crash
an hour.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
import time
import traceback
from functools import partial
from urllib.parse import quote

from django.conf import settings
from django.db import connection, transaction
from django.utils.encoding import escape_uri_path
from django.utils.log import AdminEmailHandler
from django.views.debug import ExceptionReporter, SafeExceptionReporterFilter

#: What a starred value reads as — Django's own stars.
SUBSTITUTE = SafeExceptionReporterFilter.cleansed_substitute

#: A setting, header or cookie whose name says it holds a secret.
SECRET_NAMES = re.compile(
    r"API|TOKEN|KEY|SECRET|PASS|SIGNATURE|COOKIE|AUTHORIZATION|CREDENTIAL"
    r"|PRIVATE|SALT|DSN",
    re.IGNORECASE)

#: A URL or form parameter whose name says it carries a secret. Wider than
#: SECRET_NAMES — an OAuth ``code``, a signed link's ``sig``, a ``session`` —
#: because starring a harmless parameter costs one line of the report, and
#: the settings dump would lose too much to the same list.
SECRET_PARAMS = re.compile(
    r"API|TOKEN|KEY|SECRET|PASS|SIGNATURE|SIG|COOKIE|AUTH|CREDENTIAL|PRIVATE"
    r"|SALT|SESSION|CSRF|CODE|OTP|PIN",
    re.IGNORECASE)

#: Values shorter than this are starred where they stand but not hunted for
#: in the exception's text: "on" or "1" would star half the message.
MIN_SCRUBBED_LENGTH = 6

#: The same crash is mailed at most once in this many seconds.
REPEAT_SECONDS = 60 * 60

#: Routes whose URL path is a credential: every value one captures is starred
#: (2026-10-01, the review). The vault's peer routes carry a grant's id and
#: its magic token — the whole of what a peer shows to be let in.
SECRET_ROUTES = frozenset({
    "vault:peer_manifest",
    "vault:peer_files",
    "vault:peer_file_detail",
    "vault:peer_file_download",
})
#: On any other route, a value captured under a name like these is starred.
#: Narrower than SECRET_NAMES: a file's ``key`` or a plan's ``plan_key`` in a
#: path names a thing, and the report needs it to say which.
SECRET_PATH_NAMES = re.compile(r"TOKEN|SECRET|PASSWORD|SIGNATURE|CREDENTIAL",
                               re.IGNORECASE)

_URL_PASSWORD = re.compile(
    r"(?P<head>\b[a-z][a-z0-9+.\-]*://[^\s:/@]*:)[^\s@/]+@", re.IGNORECASE)
_PARAMETER = re.compile(
    r"(?P<head>(?:^|[?&;])(?P<name>[^=&;#?\s]+)=)[^&;#\s]*")


def hide_secrets_in(text: str, known=()) -> str:
    """``text`` with each of the ``known`` values, the password of a URL and
    the value of a parameter whose name says secret starred."""
    for value in known:
        text = text.replace(value, SUBSTITUTE)
    text = _URL_PASSWORD.sub(lambda m: f"{m.group('head')}{SUBSTITUTE}@", text)

    def parameter(match):
        if SECRET_PARAMS.search(match.group("name")):
            return match.group("head") + SUBSTITUTE
        return match.group(0)

    return _PARAMETER.sub(parameter, text)


def path_secrets(request) -> list[str]:
    """The secrets ``request``'s URL path carries, longest first: every value
    a route in SECRET_ROUTES captured, and on any route a value captured under
    a name SECRET_PATH_NAMES matches — as the path spells it, and as the
    request URL percent-encodes it. Nothing for a path no route takes."""
    if request is None:
        return []
    try:
        match = getattr(request, "resolver_match", None)
        if match is None:
            # A crash before the URL was resolved — in a middleware.
            from django.urls import resolve

            match = resolve(request.path_info, getattr(request, "urlconf", None))
    except Exception:  # noqa: BLE001 - a path no route takes captures nothing
        return []
    whole = match.view_name in SECRET_ROUTES
    values = [str(value) for name, value in match.kwargs.items()
              if whole or SECRET_PATH_NAMES.search(name)]
    if whole:
        values += [str(value) for value in match.args]
    found = set()
    for value in values:
        if len(value) >= MIN_SCRUBBED_LENGTH:
            found.update({value, escape_uri_path(value), quote(value, safe="")})
    return sorted(found, key=len, reverse=True)


def _star(text: str, values) -> str:
    for value in values:
        text = text.replace(value, SUBSTITUTE)
    return text


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item)


def _secret_strings(key, value):
    """The strings the filter stars inside one setting or header: all of a
    secret one's, and those under a secret key at any depth."""
    if isinstance(key, str) and SECRET_NAMES.search(key):
        yield from _strings(value)
    elif isinstance(value, dict):
        for inner_key, inner in value.items():
            yield from _secret_strings(inner_key, inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from _secret_strings("", inner)


class PlatformExceptionReporterFilter(SafeExceptionReporterFilter):
    """Django's filter, with the platform's secret names, URL passwords, and
    every cookie and POST value starred (the module docstring has the rules)."""

    hidden_settings = SECRET_NAMES

    def cleanse_setting(self, key, value):
        cleansed = super().cleanse_setting(key, value)
        if isinstance(cleansed, str) and cleansed != self.cleansed_substitute:
            return hide_secrets_in(cleansed)
        return cleansed

    def get_safe_cookies(self, request):
        if not hasattr(request, "COOKIES"):
            return {}
        return {name: self.cleansed_substitute for name in request.COOKIES}

    def get_post_parameters(self, request):
        # DEBUG shows the developer what was posted, as Django always has;
        # this filter guards what leaves the server, and a mail only ever
        # leaves with DEBUG off.
        if request is None or not self.is_active(request):
            return super().get_post_parameters(request)
        cleansed = request.POST.copy()
        for name in cleansed:
            cleansed[name] = self.cleansed_substitute
        return cleansed


class PlatformExceptionReporter(ExceptionReporter):
    """Django's report, with secret GET parameters starred and the exception's
    text and the request URL scrubbed of every value the filter stars."""

    def get_traceback_data(self):
        data = super().get_traceback_data()
        known = self.secret_values()

        def scrub(text):
            return hide_secrets_in(str(text), known)

        for key in ("exception_value", "exception_notes", "request_insecure_uri"):
            if data.get(key):
                data[key] = scrub(data[key])
        for frame in data.get("frames") or ():
            if frame.get("exc_cause") is not None:
                frame["exc_cause"] = scrub(frame["exc_cause"])
        if self.request is not None:
            data["request_GET_items"] = [
                (name, SUBSTITUTE if SECRET_PARAMS.search(name) else value)
                for name, value in self.request.GET.items()]
        return data

    def get_traceback_text(self):
        # The template reads the path straight off the request ("at
        # /vault/peer/…"), and META carries it as PATH_INFO and the raw URI:
        # a path that is a credential is starred in the whole text.
        return _star(super().get_traceback_text(), path_secrets(self.request))

    def secret_values(self) -> list[str]:
        """Every value the report stars, longest first — so that a secret
        which contains another is starred whole."""
        found = set()
        for name in dir(settings):
            if name.isupper():
                found.update(_secret_strings(name, getattr(settings, name, None)))
        request = self.request
        if request is not None:
            for name, value in getattr(request, "META", {}).items():
                found.update(_secret_strings(name, value))
            found.update(getattr(request, "COOKIES", {}).values())
            for _name, values in request.POST.lists():
                found.update(v for v in values if isinstance(v, str))
            for name, values in request.GET.lists():
                if SECRET_PARAMS.search(name):
                    found.update(values)
            found.update(path_secrets(request))
        return sorted((v for v in found if len(v) >= MIN_SCRUBBED_LENGTH),
                      key=len, reverse=True)


def crash_signature(exc_info) -> str:
    """The exception's type and the lines of code it was raised along: one
    bug met by many visitors on the same path is one crash."""
    exc_type, _value, tb = exc_info
    where = [f"{frame.filename}:{frame.lineno}" for frame in traceback.extract_tb(tb)]
    text = "|".join([f"{exc_type.__module__}.{exc_type.__qualname__}", *where])
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:32]


#: What this process mailed while the shared cache could not answer:
#: signature -> when (time.monotonic). Bounded by MAILED_HERE_LIMIT.
_mailed_here: dict[str, float] = {}
MAILED_HERE_LIMIT = 1000


def _first_here(signature: str) -> bool:
    """Is ``signature`` new to this process within REPEAT_SECONDS? Noted if so."""
    now = time.monotonic()
    for known, when in list(_mailed_here.items()):
        if now - when >= REPEAT_SECONDS:
            _mailed_here.pop(known, None)
    if signature in _mailed_here:
        return False
    if len(_mailed_here) >= MAILED_HERE_LIMIT:
        _mailed_here.clear()
    _mailed_here[signature] = now
    return True


class CrashMailFilter(logging.Filter):
    """Pass a django.request record on to the error mail only when it is a
    crash nobody was mailed about in the last REPEAT_SECONDS."""

    def filter(self, record):
        exc_info = record.exc_info
        if not exc_info or exc_info[0] is None:
            return False
        # Nobody to mail: building the report would be for nothing.
        if not getattr(settings, "ADMINS", None):
            return False
        signature = crash_signature(exc_info)
        try:
            from django.core.cache import cache

            first = cache.add(f"toto:crash-mail:{signature}", 1, timeout=REPEAT_SECONDS)
        except Exception:  # noqa: BLE001 - a broken cache never silences a crash
            first = None
        # django-redis answers None, not False, when Redis is unreachable. A
        # broken cache never silences a crash, and never lets one mail per
        # visit either: this process remembers what it mailed (2026-10-01).
        if first is None:
            return _first_here(signature)
        return first is not False


class PlatformAdminEmailHandler(AdminEmailHandler):
    """Django's error mail, with the secrets of a URL path starred in its
    subject, and sent after the commit (2026-10-01, the review).

    Django writes the subject and the line above the report from the log
    message — "Internal Server Error: <path>" — before any reporter sees the
    request, so ``path_secrets`` are starred here, on a copy of the record:
    the console log keeps what it always had. The report itself is the
    reporter's (``PlatformExceptionReporter``). Logged inside a transaction,
    the mail waits for its commit, and a rollback drops it; outside one —
    every crashing request, which Django logs once the view's transaction
    has ended — it leaves at once.
    """

    def emit(self, record):
        secrets = path_secrets(getattr(record, "request", None))
        if secrets:
            record = copy.copy(record)
            record.msg, record.args = _star(record.getMessage(), secrets), None
        super().emit(record)

    def send_mail(self, subject, message, *args, **kwargs):
        send = partial(super().send_mail, subject, message, *args, **kwargs)
        if connection.in_atomic_block:
            transaction.on_commit(send, robust=True)
        else:
            send()
