from django.apps import AppConfig


class SSOClientConfig(AppConfig):
    name = "toto.sso_client"
    verbose_name = "SSO Client (Consumer)"
    default_auto_field = "django.db.models.BigAutoField"

    def get_config(self):
        """Read OIDC consumer config from the active OIDCProviderConfig row.

        **The SSO_CLIENT_SECRET environment override is gone.** It used to take
        precedence over the stored value, so a stale variable silently beat
        whatever an operator set in the admin — a pairing could report success
        while every login failed with ``invalid_client``, with no log line and
        nothing on the page. The secret now comes from the vault and from nowhere
        else. ``sso_client.pairing`` refuses to pair at all while that variable is
        still set, because removing the read is not enough: somebody who set it
        expects it to matter and has to be told it does not.
        """
        from toto.sso_client.models import OIDCProviderConfig

        record = (
            OIDCProviderConfig.objects
            .filter(active=True)
            .select_related("secret")
            .order_by("-imported_at")
            .first()
        )
        if not record:
            return {
                "label": "", "portal_url": "", "client_id": "", "client_secret": "",
                "scopes": "openid email profile", "trusted": False,
                "redirect_uris": [],
                "authorization_endpoint": "", "token_endpoint": "",
                "userinfo_endpoint": "", "jwks_uri": "", "issuer": "",
            }

        return {
            # The provider's DISPLAY name — the hybrid login page shows
            # "Sign in with <label>".
            "label": record.label,
            "portal_url": record.portal_url,
            "client_id": record.client_id,
            "client_secret": _client_secret(record),
            "scopes": record.scopes,
            "trusted": record.trusted,
            "redirect_uris": record.redirect_uris_list(),
            # Learned at pairing rather than string-built per request. Blank on a
            # row that predates pairing; callers fall back — see views.
            "authorization_endpoint": record.authorization_endpoint,
            "token_endpoint": record.token_endpoint,
            "userinfo_endpoint": record.userinfo_endpoint,
            "jwks_uri": record.jwks_uri,
            "issuer": record.issuer,
        }


def _client_secret(record) -> str:
    """The decrypted client secret, or "" if the vault will not open.

    Returning "" rather than raising is deliberate: this is called while building
    a login redirect, and a locked vault must degrade to a failed token exchange
    with a logged reason, not a 500 on the login page.
    """
    if record.secret_id is None:
        return ""
    from toto.sso_core import vault

    try:
        return vault.read_secret(record.secret, create=False)
    except Exception as exc:                    # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(
            "Could not decrypt the OIDC client secret: %s", exc,
        )
        return ""
