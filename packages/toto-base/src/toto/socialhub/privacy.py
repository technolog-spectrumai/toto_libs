"""The privacy notice (2026-10-01, RODO / GDPR): publishing a version, the
placeholder text version 1 ships with, and a host's own text.

``publish`` is the one writer. It never edits a version: it adds the next
number, so whatever an applicant accepted stays readable at its own address
(``socialhub:privacy_notice_version``), and it records
``PRIVACY.NOTICE_PUBLISHED`` on the audit chain — the version and how long
each text is, never the text itself.

The PLACEHOLDER below is exactly that: the shape a notice under the GDPR
usually takes, so the pages and the application have something real-sized to
show, with every fact the platform cannot know left in brackets. The
organisation's own text replaces it through the editor
(``socialhub:privacy_notice_edit``) — the owner's, not the code's.

A host may ship its own text instead (2026-10-01, 37c.16):
``PRIVACY_NOTICE_TEXTS = {"pl": path, "en": path}`` names two plain-text
files, and ``seed_notice`` — the ingress's — publishes them as version 1 on a
fresh database, and as the next version where the current one is still the
placeholder. What such a text leaves for its owner to fill in is marked
``[[…]]`` (``markers``), and the ingress names it when it seeds.

A corrected host text reaches a running platform the same way (37c.32): where
the current version is one the platform seeded itself (``seeded``) and the
host's files now say something else, the next start publishes them as the
next version. Never over a version a person published — an empty
``published_by`` does not tell one apart, since an erased superuser's version
has one too, so the platform marks its own.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.db.models import Max

#: The longest text a version may carry, per language. A notice is a few
#: pages; this is a guard against a pasted book, not an editorial rule. It was
#: 50,000 until 2026-10-01 (37c.16), when a real notice — zenobia's Polish,
#: written from what the platform does — came out at about 50,200.
MAX_TEXT = 100_000

#: A place a host's text leaves for its owner to fill in: ``[[UZUPEŁNIJ: …]]``
#: or ``[[FILL IN: …]]``. Never in the placeholder, whose brackets are single.
MARKER = re.compile(r"\[\[(.+?)\]\]", re.S)

PLACEHOLDER_MARK_EN = "PLACEHOLDER — replace with your organisation's privacy notice"
PLACEHOLDER_MARK_PL = "PLACEHOLDER — zastąp tę treść polityką prywatności swojej organizacji"

PLACEHOLDER_EN = f"""{PLACEHOLDER_MARK_EN}

This text is an example shipped with the platform. It is not legal advice and it does not describe your organisation. Replace every part in [brackets] and have the result checked before you rely on it.

1. Who is responsible for your data

The controller of your personal data is [organisation name], [address], [registration number]. You can reach us at [e-mail address]. [If appointed: our data protection officer can be reached at [e-mail address].]

2. What data we process

When you apply for membership: your name, username, e-mail address, postal address and the location you choose to share, and the community you apply to.
When you use the platform: your profile, the files, documents and messages you create, the communities and clearances you hold, and technical records of your sign-ins (time, network address, browser) kept for security.

3. Why we process it, and on what basis

- To consider your application and run your membership — the contract between you and us (Article 6(1)(b) GDPR).
- To keep the platform and your account secure, including the audit record of important actions — our legitimate interest (Article 6(1)(f) GDPR).
- To meet legal obligations, such as accounting — Article 6(1)(c) GDPR.
[Add any processing based on consent, and how it can be withdrawn.]

4. Who receives your data

Other members see what you place on your public profile. We use [hosting provider, e-mail provider] to run the platform; they process data only on our instructions. We do not sell your data. [State any transfer outside the European Economic Area and its safeguards, or that there is none.]

5. How long we keep it

An application that is not completed is deleted after [period]. Your account data is kept while you are a member and deleted [period] after your membership ends, unless the law requires us to keep it longer. Security records are kept for [period].

6. Your rights

You have the right to access your data and receive a copy of it, to have it corrected, to have it erased, to restrict or object to its processing, and to data portability. You can download a copy of your data from My account and ask for erasure there; we answer within one month. You also have the right to lodge a complaint with a supervisory authority — in Poland, the President of the Personal Data Protection Office (Prezes Urzędu Ochrony Danych Osobowych, ul. Stawki 2, 00-193 Warszawa).

7. Automated decisions

We make no decisions about you based solely on automated processing, including profiling. [Change this if it is not true.]

8. Changes to this notice

When this notice changes we publish a new version with its own number and date; earlier versions stay available. The version you accepted when you applied is recorded with your application.
"""

PLACEHOLDER_PL = f"""{PLACEHOLDER_MARK_PL}

Ten tekst jest przykładem dostarczonym z platformą. Nie jest poradą prawną i nie opisuje Twojej organizacji. Zastąp każdy fragment w [nawiasach] i daj wynik do sprawdzenia, zanim zaczniesz na nim polegać.

1. Kto odpowiada za Twoje dane

Administratorem Twoich danych osobowych jest [nazwa organizacji], [adres], [numer rejestrowy]. Możesz się z nami skontaktować pod adresem [adres e-mail]. [Jeśli wyznaczono: z naszym inspektorem ochrony danych skontaktujesz się pod adresem [adres e-mail].]

2. Jakie dane przetwarzamy

Przy wniosku o członkostwo: imię i nazwisko, nazwę użytkownika, adres e-mail, adres pocztowy i lokalizację, którą zdecydujesz się udostępnić, oraz społeczność, do której składasz wniosek.
Podczas korzystania z platformy: Twój profil, tworzone przez Ciebie pliki, dokumenty i wiadomości, społeczności i poświadczenia, które posiadasz, oraz techniczne zapisy Twoich logowań (czas, adres sieciowy, przeglądarka) przechowywane ze względów bezpieczeństwa.

3. W jakim celu i na jakiej podstawie

- Aby rozpatrzyć Twój wniosek i prowadzić Twoje członkostwo — umowa między Tobą a nami (art. 6 ust. 1 lit. b RODO).
- Aby zapewnić bezpieczeństwo platformy i Twojego konta, w tym rejestr audytowy ważnych działań — nasz prawnie uzasadniony interes (art. 6 ust. 1 lit. f RODO).
- Aby wypełnić obowiązki prawne, np. księgowe — art. 6 ust. 1 lit. c RODO.
[Dodaj przetwarzanie oparte na zgodzie i sposób jej wycofania.]

4. Kto otrzymuje Twoje dane

Inni członkowie widzą to, co umieścisz w swoim publicznym profilu. Do prowadzenia platformy korzystamy z usług [dostawca hostingu, dostawca poczty e-mail]; przetwarzają oni dane wyłącznie na nasze polecenie. Nie sprzedajemy Twoich danych. [Opisz przekazywanie danych poza Europejski Obszar Gospodarczy i jego zabezpieczenia albo zaznacz, że go nie ma.]

5. Jak długo przechowujemy dane

Niedokończony wniosek usuwamy po [okres]. Dane konta przechowujemy, dopóki jesteś członkiem, i usuwamy [okres] po zakończeniu członkostwa, chyba że prawo wymaga dłuższego przechowywania. Zapisy bezpieczeństwa przechowujemy przez [okres].

6. Twoje prawa

Masz prawo dostępu do swoich danych i otrzymania ich kopii, ich sprostowania, usunięcia, ograniczenia przetwarzania, wniesienia sprzeciwu oraz przenoszenia danych. Kopię swoich danych pobierzesz w zakładce Moje konto i tam też złożysz wniosek o usunięcie; odpowiadamy w ciągu miesiąca. Masz też prawo wnieść skargę do organu nadzorczego — Prezesa Urzędu Ochrony Danych Osobowych (ul. Stawki 2, 00-193 Warszawa).

7. Zautomatyzowane decyzje

Nie podejmujemy wobec Ciebie decyzji opartych wyłącznie na zautomatyzowanym przetwarzaniu, w tym profilowaniu. [Zmień to, jeśli nie jest prawdą.]

8. Zmiany tej informacji

Gdy ta informacja się zmienia, publikujemy nową wersję z własnym numerem i datą; wcześniejsze wersje pozostają dostępne. Wersja zaakceptowana przy składaniu wniosku jest zapisana razem z Twoim wnioskiem.
"""


def clean_text(value) -> str:
    """A text as it is stored: line endings made ``\\n``, the ends trimmed."""
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def publish(*, text_pl: str, text_en: str, user=None, seeded: bool = False):
    """Publish a new version and return it: the next number, now, by ``user``
    — or, ``seeded``, by the platform itself (``seed_notice`` alone says so).

    Two editors publishing at once both read the same highest number; the
    unique ``version`` refuses the second insert, which then takes the number
    after — once, and a third collision is an error worth seeing.
    """
    from .models import PrivacyNotice

    text_pl, text_en = clean_text(text_pl), clean_text(text_en)
    if not text_pl or not text_en:
        raise ValueError("A privacy notice needs its text in both languages.")
    if len(text_pl) > MAX_TEXT or len(text_en) > MAX_TEXT:
        raise ValueError("A privacy notice text is too long.")
    for attempt in range(2):
        latest = PrivacyNotice.objects.aggregate(top=Max("version"))["top"] or 0
        try:
            with transaction.atomic():
                notice = PrivacyNotice.objects.create(
                    version=latest + 1, text_pl=text_pl, text_en=text_en,
                    published_by=user if getattr(user, "pk", None) else None,
                    seeded=bool(seeded))
                break
        except IntegrityError:
            if attempt:
                raise
    from . import audit

    audit.notice_published(notice, previous=latest or None)
    return notice


def markers(text) -> list[str]:
    """What ``text`` still leaves to fill in, by name (what stands between
    ``[[`` and ``]]``), each once, in the order they first appear."""
    names = []
    for match in MARKER.finditer(text or ""):
        name = " ".join(match.group(1).split())
        if name not in names:
            names.append(name)
    return names


def is_placeholder(notice) -> bool:
    """Whether ``notice`` still shows the placeholder this module ships — in
    either language, because a version half replaced still shows it."""
    return notice is not None and (
        clean_text(notice.text_pl).startswith(PLACEHOLDER_MARK_PL)
        or clean_text(notice.text_en).startswith(PLACEHOLDER_MARK_EN))


def host_texts():
    """The host's own notice as ``(text_pl, text_en)``, or None when its
    settings name none (``PRIVACY_NOTICE_TEXTS``). Named but missing or
    unreadable is a misconfiguration, and said as one: a platform must not
    quietly come up on the placeholder its host meant to replace."""
    paths = getattr(settings, "PRIVACY_NOTICE_TEXTS", None)
    if not paths:
        return None
    texts = []
    for language in ("pl", "en"):
        path = paths.get(language) if isinstance(paths, dict) else None
        if not path:
            raise ImproperlyConfigured(f"PRIVACY_NOTICE_TEXTS names no {language!r} text.")
        try:
            texts.append(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise ImproperlyConfigured(
                f"PRIVACY_NOTICE_TEXTS[{language!r}] cannot be read ({path}): {exc}") from exc
    return texts[0], texts[1]


def differs(notice, texts) -> bool:
    """Whether ``notice`` says something other than ``texts`` (``(pl, en)``),
    compared as stored (``clean_text``): a host file saved with other line
    endings or a trailing newline has not changed."""
    return ((clean_text(notice.text_pl), clean_text(notice.text_en))
            != (clean_text(texts[0]), clean_text(texts[1])))


def seed_notice():
    """What the ingress publishes (``ingress_socialhub``, every mode but none):
    version 1 when there is no version at all — the host's own text where its
    settings name one, the placeholder otherwise — and, since 2026-10-01
    (37c.16), the host's text as the NEXT version where the current one is
    still the placeholder, so a platform seeded before its host had a text
    moves off it by itself; and (37c.32) where the current one is the
    platform's own seed and the host's files have changed since, so a
    corrected text reaches a running platform at its next start. Through
    ``publish``, marked ``seeded``: a new version, audited, never an edit. A
    current version a person published is left alone, whoever they were and
    whatever the files say. Returns the notice published, or None."""
    from .models import PrivacyNotice

    texts = host_texts()
    current = PrivacyNotice.current()
    if current is None:
        text_pl, text_en = texts or (PLACEHOLDER_PL, PLACEHOLDER_EN)
    elif texts is not None and is_placeholder(current):
        text_pl, text_en = texts
    elif texts is not None and current.seeded and differs(current, texts):
        text_pl, text_en = texts
    else:
        return None
    return publish(text_pl=text_pl, text_en=text_en, user=None, seeded=True)


#: The name from when the ingress could seed only the placeholder. Other
#: hosts call it; on a host that names no texts it does exactly that still.
seed_placeholder = seed_notice
