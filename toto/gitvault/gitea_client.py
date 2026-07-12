"""Minimal Gitea REST client (requests) for auto-provisioning per-user
access — runs as the ``portal-svc`` admin account that provision_oauth.sh
creates alongside the OIDC source.

Flow per user: ensure the Gitea user exists (matching the OIDC identity —
username + email — so ACCOUNT_LINKING=auto links their SSO login), mint a
``portal-gitvault`` access token via the admin API, store it encrypted on
GiteaAccount. Repos are then created with the USER'S token so ownership and
attribution are correct.
"""

from __future__ import annotations

import re
import secrets as pysecrets

import requests
from django.conf import settings
from django.utils.text import slugify

from .models import GiteaAccount
from .services import GitvaultError

TOKEN_NAME = "portal-gitvault"
_USERNAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _base() -> str:
    return getattr(settings, "GITEA_INTERNAL_URL", "http://gitea:3000").rstrip("/")


def _admin_auth() -> tuple[str, str]:
    password = getattr(settings, "GITEA_SVC_PASSWORD", "")
    if not password:
        raise GitvaultError("GITEA_SVC_PASSWORD is not configured")
    return ("portal-svc", password)


def _request(method: str, path: str, auth, **kwargs) -> requests.Response:
    resp = requests.request(
        method, f"{_base()}/api/v1{path}", auth=auth, timeout=15, **kwargs
    )
    return resp


def sanitize_username(username: str) -> str:
    """Gitea usernames: alphanumeric, ``-``, ``_``, ``.`` — must not be all
    dots or reserved. Portal usernames are close already; strip the rest."""
    name = _USERNAME_RE.sub("-", username).strip("-.") or "user"
    return name[:40]


def _user_email(user) -> str:
    # Must match the OIDC email claim so ACCOUNT_LINKING=auto links SSO logins.
    return user.email or f"{user.username}@noreply.localhost"


def ensure_account(user) -> GiteaAccount:
    """GiteaAccount with a working token, creating the Gitea user and/or token
    on first use."""
    account, _ = GiteaAccount.objects.get_or_create(
        user=user, defaults={"username": sanitize_username(user.username)}
    )
    _ensure_gitea_user(account, user)
    if not account.token_encrypted:
        _mint_token(account)
    return account


def _ensure_gitea_user(account: GiteaAccount, user) -> None:
    resp = _request("GET", f"/users/{account.username}", auth=_admin_auth())
    if resp.status_code == 200:
        return
    if resp.status_code != 404:
        raise GitvaultError(f"gitea user lookup failed: HTTP {resp.status_code}")
    create = _request(
        "POST", "/admin/users", auth=_admin_auth(),
        json={
            "username": account.username,
            "email": _user_email(user),
            "password": pysecrets.token_urlsafe(24),
            "must_change_password": False,
            "source_id": 0,
        },
    )
    if create.status_code not in (200, 201):
        raise GitvaultError(f"gitea user creation failed: {create.text[:200]}")


def _mint_token(account: GiteaAccount) -> None:
    auth = _admin_auth()
    # A stale token with our name (value lost) blocks re-creation — drop it.
    existing = _request("GET", f"/users/{account.username}/tokens", auth=auth)
    if existing.status_code == 200:
        for tok in existing.json():
            if tok.get("name") == TOKEN_NAME:
                _request("DELETE", f"/users/{account.username}/tokens/{tok['id']}", auth=auth)
    resp = _request(
        "POST", f"/users/{account.username}/tokens", auth=auth,
        # write:user (not just read) — repo creation lives under /user/repos,
        # which Gitea gates on the `user` scope category.
        json={"name": TOKEN_NAME, "scopes": ["write:repository", "write:user"]},
    )
    if resp.status_code not in (200, 201):
        raise GitvaultError(f"gitea token creation failed: {resp.text[:200]}")
    account.set_token(resp.json()["sha1"])
    account.save(update_fields=["token_encrypted"])


def create_repo(account: GiteaAccount, name: str) -> str:
    """Create a private repo owned by the user (their own token); returns the
    final repo name (slugified, collision-suffixed)."""
    base_name = slugify(name) or "vault-repo"
    token_auth = {"Authorization": f"token {account.get_token()}"}
    candidate, n, reminted = base_name, 1, False
    while n <= 50:
        resp = requests.post(
            f"{_base()}/api/v1/user/repos",
            headers=token_auth,
            json={"name": candidate, "private": True, "auto_init": False},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return candidate
        if resp.status_code == 409:  # name taken — suffix and retry
            candidate = f"{base_name}-{n}"
            n += 1
            continue
        if resp.status_code == 401 and not reminted:
            # Token revoked/aged out — re-mint once and retry.
            _mint_token(account)
            token_auth = {"Authorization": f"token {account.get_token()}"}
            reminted = True
            continue
        raise GitvaultError(f"gitea repo creation failed: {resp.text[:200]}")
    raise GitvaultError("gitea repo creation failed: could not find a free name")
