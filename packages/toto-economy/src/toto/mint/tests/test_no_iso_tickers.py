"""No ticker in the economy's tests is a living currency's code (economy.md,
"No real currency, anywhere").

A test may engrave a currency under any ticker that reads well, and three of
the mint's read too well: MNT for minting, NOK for "no key" and AUD for an
audit are the Mongolian tugrik, the Norwegian krone and the Australian dollar.
They became MINT, NOKY and AUDT on 2026-10-01. These tests keep every test
module of the economy wheel's apps from engraving, posting or looking up an
ISO 4217 code again: written in capitals anywhere, or in any case where it is
given as a ``unit_name`` (the mint desk upper-cases what its form posts).
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from django.test import SimpleTestCase

#: The apps of the toto-economy wheel. Their tests are where currencies are
#: engraved under throwaway tickers.
ECONOMY_APPS = ("assets", "clearing", "mana", "mint", "tariffs", "tax")

#: ISO 4217 List One as SIX published it on 2026-09-17: the code of every
#: living currency, fund, precious metal and unit of account, and XTS, which
#: ISO keeps for tests. A test ticker needs none of them. Withdrawn codes
#: (List Three, ISO's historic denominations) are left out, as the rule
#: leaves historic money alone.
ISO_4217 = frozenset("""
AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BOV BRL BSD
BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK
DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF
IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP
LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD
NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD
SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD
TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAD XAF XAG XAU XBA
XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWG
""".split())

#: The argument and form field a ticker is given as. There it may be written
#: in lower case: the mint desk upper-cases what its form posts.
TICKER_FIELDS = ("unit_name",)


def _toto_root() -> Path:
    """The `toto/` dir holding the economy apps — `src/toto` in the wheel's
    source tree, `site-packages/toto` where the wheel is installed."""
    return Path(__file__).resolve().parents[2]


def _test_modules(root: Path):
    """(app, path) for every test module and test helper of the economy apps,
    except this one, which names the codes on purpose."""
    here = Path(__file__).resolve()
    for app in ECONOMY_APPS:
        for path in sorted((root / app).rglob("*.py")):
            folders = path.relative_to(root / app).parts[:-1]
            if path.resolve() == here:
                continue
            if "tests" in folders or path.name.startswith("test"):
                yield app, path


def iso_tickers(source: str) -> list[tuple[int, str]]:
    """(line, ticker) for each ISO 4217 code ``source`` writes as a ticker: a
    string in capitals anywhere, or in any case given as a ``unit_name``.
    "bob" is a user, not the boliviano, until it is given as a ticker."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and node.value in ISO_4217:
            found.append((node.lineno, node.value))
        if isinstance(node, ast.keyword) and node.arg in TICKER_FIELDS:
            given = [node.value]
        elif isinstance(node, ast.Dict):
            given = [value for key, value in zip(node.keys, node.values)
                     if isinstance(key, ast.Constant)
                     and key.value in TICKER_FIELDS]
        else:
            continue
        for value in given:
            text = value.value if isinstance(value, ast.Constant) else None
            # A ticker in capitals was counted above already.
            if (isinstance(text, str) and text != text.upper()
                    and text.upper() in ISO_4217):
                found.append((value.lineno, text))
    return sorted(found)


class NoIsoTickerTests(SimpleTestCase):

    def test_no_economy_test_gives_a_living_currency_as_a_ticker(self):
        root = _toto_root()
        offenders = [
            f"{path.relative_to(root)}:{line}: {ticker}"
            for _app, path in _test_modules(root)
            for line, ticker in iso_tickers(path.read_text(encoding="utf-8"))]
        self.assertEqual(
            offenders, [],
            "a test ticker is an ISO 4217 currency code: add a letter (NOK "
            "became NOKY) or pick another")

    def test_the_scan_reads_every_economy_app(self):
        """A scan that read nothing would find nothing and pass."""
        root = _toto_root()
        found = list(_test_modules(root))
        self.assertEqual({app for app, _path in found}, set(ECONOMY_APPS))
        read = {path.relative_to(root).as_posix() for _app, path in found}
        for module in ("mint/tests/test_more_desk.py",
                       "mint/tests/test_more_records.py",
                       "assets/tests.py", "assets/testing.py",
                       "mana/tests/unit/test_faucets.py"):
            self.assertIn(module, read)
        self.assertNotIn("assets/models.py", read)
        self.assertNotIn("mint/tests/test_no_iso_tickers.py", read)

    def test_a_ticker_is_found_however_it_is_given(self):
        source = textwrap.dedent('''
            asset = self._engraved("NOK", minted="10")
            make_asset(unit_name="aud")
            self.client.post(url, {"unit_name": "usd", "name": "x"})
            Asset.objects.get(unit_name="NOKY")
            User.objects.create_user("bob")
            self.assertIn("Minted 1000 base units of MINT", text)
            self.client.post(url, {"unit_name": "dkc"})
        ''')
        self.assertEqual(iso_tickers(source),
                         [(2, "NOK"), (3, "aud"), (4, "usd")])

    def test_the_list_is_list_one_as_published(self):
        self.assertEqual(len(ISO_4217), 178)
        self.assertTrue(all(len(code) == 3 and code.isalpha() and code.isupper()
                            for code in ISO_4217))
        # The three the mint's tests used, and the three the rule names.
        self.assertLessEqual({"MNT", "NOK", "AUD", "PLN", "USD", "EUR"},
                             ISO_4217)
        # The platform's own three-letter tickers are nobody's currency.
        self.assertFalse({"ASR", "RED"} & ISO_4217)
