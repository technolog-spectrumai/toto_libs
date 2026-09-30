"""The peer API's client half — one class, one HTTP seam, honest stamps.

**Every call stamps the peer row.** ``last_ok_at`` when the peer answered,
``last_error`` when it did not (or answered with an auth/server failure) —
via queryset ``.update()``, so a background refresh never races an admin
save. Badges and banners read these columns and nothing else: no page render
ever probes a peer (the jess rule — a third party's latency has no place on
a request's critical path).

A per-file 404 or an encrypted-file 409 is the peer ANSWERING, not the link
failing, so those stamp ok and raise :class:`PeerStatusError` for the caller
to interpret. Transport failures, auth refusals and 5xx stamp the error.

**Redirects are never followed.** The SSRF guard (``outbound``) checks the
peer's base URL once, here; a redirect would send the next request — with
the api key header, and the body of whatever answered — to an address that
check never saw (``169.254.169.254``, an internal service). The peer API
never redirects, so a 3xx is a broken or hostile peer: stamped, refused, and
its ``Location`` never fetched.

``_http()`` is the module-level seam tests patch (clearing's stated
convention); ``requests`` is imported nowhere else, so a host without the
``remote-vault`` extra pays nothing until a peer is actually used.

URLs are built from :data:`toto.vault.peering.PEER_PATH` — the same constant
the server's urls.py encodes, so the two halves cannot drift silently.
"""
from __future__ import annotations

from django.utils import timezone

from .peering import PEER_PATH

try:  # celery is optional in toto-base; without it nothing sends this
    from celery.exceptions import SoftTimeLimitExceeded
except ImportError:  # pragma: no cover
    class SoftTimeLimitExceeded(Exception):
        pass

#: (connect, read) — a peer that cannot accept a connection in 5s is down;
#: a streamed download may legitimately take longer between chunks.
DEFAULT_TIMEOUT = (5, 30)

API_KEY_HEADER = "X-Vault-Api-Key"


def _http():
    """The one place ``requests`` enters. Patch me in tests."""
    import requests

    return requests


class PeerError(RuntimeError):
    """The peer could not be used. The message is an operator-facing
    sentence, already naming the peer."""


class PeerStatusError(PeerError):
    """The peer answered with an HTTP error. Carries the status and, when
    the body had one, the machine ``reason`` (e.g. encrypted-non-portable)."""

    def __init__(self, message, status, reason=""):
        super().__init__(message)
        self.status = status
        self.reason = reason


def _short_body(resp) -> str:
    try:
        data = resp.json()
        return str(data.get("error") or data)[:200]
    except Exception:  # noqa: BLE001 - any body shape must produce a sentence
        return (resp.text or "")[:200]


def _reason(resp) -> str:
    try:
        return str(resp.json().get("reason") or "")
    except Exception:  # noqa: BLE001
        return ""


class PeerClient:
    """Speaks to one :class:`~toto.vault.peering.BucketPeer`."""

    def __init__(self, peer, *, api_key: str | None = None):
        self.peer = peer
        #: A sealed api key an operator just opened. When absent the legacy
        #: path applies and peer.get_api_key() reads the Fernet column — which
        #: is what keeps every already-paired peer working the day sealed
        #: credentials ship.
        self._api_key = api_key or None
        # The SSRF chokepoint for every peer call. One check here covers
        # manifest / list / meta / download / upload / delete, because all six
        # go through _request() and all six build on self._base.
        from .outbound import assert_outbound_allowed

        base = assert_outbound_allowed(peer.base_url, label="Peer URL")
        self._base = (base.rstrip("/") + "/vault/"
                      + PEER_PATH.format(grant_uid=peer.grant_uid,
                                         magic_token=peer.magic_token))

    # ── transport ─────────────────────────────────────────────────────────
    def _redact(self, text: str) -> str:
        """A transport exception quotes the URL it called, and that URL
        carries the grant's magic token (and the grant id): an error stamped
        on the row, shown on a page or put in JSON must carry neither."""
        text = str(text)
        for value in (self.peer.magic_token, str(self.peer.grant_uid or ""), self._api_key):
            if value and len(value) >= 6:
                text = text.replace(value, "…")
        return text

    def _stamp(self, error: str = "") -> None:
        from .peering import BucketPeer

        error = self._redact(error)
        fields = {"last_error": error}
        if not error:
            fields["last_ok_at"] = timezone.now()
        BucketPeer.objects.filter(pk=self.peer.pk).update(**fields)

    def _request(self, method, path, *, stream=False, **kwargs):
        headers = {API_KEY_HEADER: self._api_key or self.peer.get_api_key()}
        # Never a caller's choice: the guard checked self._base and nothing else.
        kwargs.pop("allow_redirects", None)
        try:
            resp = _http().request(
                method, self._base + path, headers=headers,
                timeout=DEFAULT_TIMEOUT, stream=stream, allow_redirects=False, **kwargs)
        except SoftTimeLimitExceeded:
            # The worker's clock, not the link: stamping it would badge a
            # working peer as broken, and a PeerError would fail the run.
            raise
        except Exception as exc:  # noqa: BLE001 - every transport failure, one shape
            self._stamp(f"{type(exc).__name__}: {exc}")
            raise PeerError(self._redact(
                f"{self.peer.label}: {type(exc).__name__}: {exc}")) from exc
        if 300 <= resp.status_code < 400:
            # Neither the Location nor the body: both are the redirecting
            # host's words, and the target is exactly what must stay unread.
            try:
                resp.close()
            except Exception:  # noqa: BLE001 - a fake or an already-closed response
                pass
            self._stamp(f"HTTP {resp.status_code}: a redirect, which is never followed")
            raise PeerError(self._redact(
                f"{self.peer.label} answered with a redirect (HTTP {resp.status_code}), "
                "which this server never follows: check the other Zenobia's address"))
        if resp.status_code >= 400:
            detail = _short_body(resp)
            # 404/409 are answers ABOUT A FILE from a working link; anything
            # else (403 auth, 5xx) is the link itself being broken.
            if resp.status_code in (404, 409):
                self._stamp()
            else:
                self._stamp(f"HTTP {resp.status_code}: {detail}")
            raise PeerStatusError(
                self._redact(f"{self.peer.label} answered {resp.status_code}: {detail}"),
                status=resp.status_code, reason=_reason(resp))
        self._stamp()
        return resp

    # ── the API, one method per endpoint ──────────────────────────────────
    def manifest(self) -> dict:
        return self._request("GET", "manifest/").json()

    def list_page(self, cursor: int = 0, page_size: int | None = None) -> dict:
        params = {"cursor": cursor}
        if page_size:
            params["page_size"] = page_size
        return self._request("GET", "files/", params=params).json()

    def iter_pages(self, page_size: int | None = None):
        """Yield listing pages until the keyset cursor runs dry."""
        cursor = 0
        while True:
            page = self.list_page(cursor=cursor, page_size=page_size)
            yield page
            if page.get("next_cursor") is None:
                return
            cursor = page["next_cursor"]

    def iter_files(self, page_size: int | None = None):
        for page in self.iter_pages(page_size=page_size):
            yield from page.get("files", [])

    def meta(self, key: str) -> dict:
        return self._request("GET", f"files/{key}/").json()

    def open_download(self, key: str):
        """A binary stream of the file's bytes — never the whole body in RAM."""
        resp = self._request("GET", f"files/{key}/download/", stream=True)
        resp.raw.decode_content = True
        return resp.raw

    def read(self, key: str) -> bytes:
        return self._request("GET", f"files/{key}/download/").content

    def exists(self, key: str) -> bool:
        try:
            self._request("HEAD", f"files/{key}/download/")
        except PeerStatusError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    def upload(self, fileobj, filename: str) -> dict:
        return self._request(
            "POST", "files/", files={"file": (filename, fileobj)}).json()

    def delete(self, key: str) -> dict:
        return self._request("DELETE", f"files/{key}/").json()
