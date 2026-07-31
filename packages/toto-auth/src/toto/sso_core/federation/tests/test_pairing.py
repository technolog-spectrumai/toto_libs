"""The act of federation, and every way it must refuse.

Runs in the two-sided harness, so the consumer's HTTP call to the provider goes
through the same loopback the token exchange uses.

Most of this file is adversarial on purpose. The happy path is four lines; what
earns its keep is the list of things that must NOT work, because each one is a way
a stolen or replayed pairing code could otherwise register an attacker's callback
as a trusted relying party on somebody's identity provider.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from toto.sso_core import enrollment as wire
from toto.sso_core import qr
from toto.sso_master.enrollment import EnrollmentError, mint, redeem
from toto.sso_master.models import (
    RESERVED_CLIENT_IDS,
    SSOFederationInvite,
    SSORelyingParty,
)

PROVIDER = "https://provider.test"
CONSUMER_HOST = "consumer.test"
CALLBACK = f"https://{CONSUMER_HOST}/sso/callback/"


def _mint(**kwargs):
    kwargs.setdefault("expected_host", CONSUMER_HOST)
    kwargs.setdefault("provider_url", PROVIDER)
    kwargs.setdefault("label", "Consumer")
    return mint(**kwargs)


def _request(ticket, callback=CALLBACK, label="Consumer"):
    return {"ticket": ticket, "callback_uri": callback, "label": label}


class TicketFormatTests(TestCase):
    """The QR payload. Cheap to test, and every failure here is a support call."""

    def test_a_ticket_round_trips(self):
        secret = wire.new_secret()
        ticket = wire.encode_ticket(PROVIDER, secret)
        decoded = wire.decode_ticket(ticket)
        self.assertEqual(decoded.url, PROVIDER)
        self.assertEqual(decoded.secret, secret)

    def test_a_ticket_survives_how_a_human_actually_pastes_it(self):
        """Copied off a screen it arrives with case and whitespace damage."""
        ticket = wire.encode_ticket(PROVIDER, wire.new_secret())
        mangled = f"  {ticket[:20].upper()}\n{ticket[20:]}  "
        self.assertEqual(wire.decode_ticket(mangled).url, PROVIDER)

    def test_the_secret_is_not_recoverable_from_the_stored_hash(self):
        secret = wire.new_secret()
        ticket = wire.decode_ticket(wire.encode_ticket(PROVIDER, secret))
        self.assertEqual(ticket.secret_sha256, hashlib.sha256(secret).hexdigest())
        self.assertNotIn(secret.hex(), ticket.secret_sha256)

    def test_a_code_for_something_else_is_refused_by_its_tag(self):
        """The tag byte means one paste box can route several credential kinds."""
        import base64

        raw = bytes([0x49, 1]) + wire.new_secret() + PROVIDER.encode()
        foreign = base64.b32encode(raw).decode().rstrip("=").lower()
        with self.assertRaises(wire.TicketError) as caught:
            wire.decode_ticket(foreign)
        self.assertIn("not a federation pairing code", str(caught.exception))

    def test_every_malformed_input_names_what_is_wrong(self):
        for bad in ("", "!!!!", "aaaa", "z" * 200):
            with self.subTest(bad=bad[:12]):
                with self.assertRaises(wire.TicketError):
                    wire.decode_ticket(bad)

    def test_a_future_version_says_which_side_to_upgrade(self):
        import base64

        raw = bytes([wire.TICKET_TAG, 99]) + wire.new_secret() + PROVIDER.encode()
        future = base64.b32encode(raw).decode().rstrip("=").lower()
        with self.assertRaises(wire.TicketError) as caught:
            wire.decode_ticket(future)
        self.assertIn("version", str(caught.exception).lower())


class QRTests(TestCase):
    def test_a_rendered_qr_can_be_read_back(self):
        """The trap: cv2's encoder output does not survive its own decoder.

        The raw matrix is one pixel per module with no quiet zone and decodes to
        "". If this test ever fails, check that the upscale and border are still
        applied in qr.render_data_uri.
        """
        import base64

        ticket = wire.encode_ticket(PROVIDER, wire.new_secret())
        uri = qr.render_data_uri(ticket)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        png = base64.b64decode(uri.split(",", 1)[1])
        self.assertEqual(qr.read(png), ticket)

    def test_reading_a_non_image_says_so(self):
        with self.assertRaises(qr.QRError):
            qr.read(b"this is not a png")

    def test_an_empty_qr_is_refused_rather_than_rendered_blank(self):
        with self.assertRaises(qr.QRError):
            qr.render_data_uri("")


class MintTests(TestCase):
    def test_minting_creates_a_dormant_relying_party(self):
        """Dormant matters: an abandoned invite must leave nothing usable."""
        minted = _mint()
        rp = minted.relying_party
        self.assertFalse(rp.active)
        self.assertEqual(rp.redirect_uris, "")
        self.assertTrue(rp.pairing_managed)

    def test_the_code_is_stored_only_as_a_hash(self):
        minted = _mint()
        row = SSOFederationInvite.objects.get()
        self.assertNotIn(minted.ticket, row.secret_sha256)
        self.assertEqual(len(row.secret_sha256), 64)
        # And nothing anywhere on the row holds the redeemable value.
        blob = " ".join(str(getattr(row, f.name, "")) for f in row._meta.fields)
        self.assertNotIn(minted.ticket, blob)

    def test_a_host_can_be_given_as_a_bare_name_or_a_url(self):
        for given in (CONSUMER_HOST, f"https://{CONSUMER_HOST}/", f"{CONSUMER_HOST}:8445"):
            with self.subTest(given=given):
                minted = _mint(expected_host=given)
                self.assertEqual(minted.invite.expected_host, CONSUMER_HOST)

    def test_minting_without_a_host_is_refused(self):
        with self.assertRaises(EnrollmentError) as caught:
            _mint(expected_host="")
        self.assertEqual(caught.exception.code, "bad_request")

    def test_a_reserved_client_id_cannot_be_taken_by_a_pairing(self):
        """The sidecar-starvation bug.

        An invite labelled "Gitea" slugs to `gitea`. On a database where that row
        does not exist yet — fresh install, post-RESET, or the service switched
        off — taking it would make create_relying_party raise on the real Gitea
        forever after, and the provisioning loop would stop dead there.
        """
        for label in ("Gitea", "grafana", "GITEA"):
            with self.subTest(label=label):
                minted = _mint(label=label)
                self.assertNotIn(
                    minted.relying_party.client_id, RESERVED_CLIENT_IDS,
                )

    def test_re_pairing_keeps_the_same_relying_party(self):
        """The UUID must not move: live tokens point at it.

        SSOAccessToken.client and SSOAuthorizationCode.client both FK here, so a
        second row would silently invalidate every live session while looking
        like a success.
        """
        first = _mint()
        redeem(_request(first.ticket))
        rp = SSORelyingParty.objects.get(pk=first.relying_party.pk)
        original_pk, original_client_id = rp.pk, rp.client_id

        again = _mint(relying_party=rp)
        redeem(_request(again.ticket))

        self.assertEqual(SSORelyingParty.objects.count(), 1)
        rp.refresh_from_db()
        self.assertEqual(rp.pk, original_pk)
        self.assertEqual(rp.client_id, original_client_id)


class RedeemTests(TestCase):
    def test_the_happy_path_returns_working_credentials(self):
        minted = _mint()
        grant = redeem(_request(minted.ticket))

        self.assertTrue(grant.client_secret)
        self.assertEqual(grant.client_id, minted.relying_party.client_id)
        self.assertTrue(grant.token_endpoint.endswith("/sso/token/"))
        self.assertTrue(grant.jwks_uri.endswith("/sso/jwks.json"))

        rp = SSORelyingParty.objects.get(pk=minted.relying_party.pk)
        self.assertTrue(rp.active)
        self.assertTrue(rp.verify_client_secret(grant.client_secret))

    def test_the_consumers_exact_callback_is_what_gets_registered(self):
        """Only the consumer knows its exact path, and it is compared verbatim.

        A trailing-slash difference here is the most common federation
        misconfiguration there is, which is exactly why the provider never types
        this value.
        """
        minted = _mint()
        redeem(_request(minted.ticket, callback=CALLBACK))
        rp = SSORelyingParty.objects.get(pk=minted.relying_party.pk)
        self.assertIn(CALLBACK, rp.redirect_uri_list())

    def test_re_pairing_adds_a_uri_rather_than_replacing_it(self):
        """During a domain move the old URI must keep working until cutover."""
        first = _mint()
        redeem(_request(first.ticket))
        rp = SSORelyingParty.objects.get(pk=first.relying_party.pk)

        second = _mint(expected_host="new.test", relying_party=rp)
        redeem(_request(second.ticket, callback="https://new.test/sso/callback/"))

        rp.refresh_from_db()
        self.assertIn(CALLBACK, rp.redirect_uri_list())
        self.assertIn("https://new.test/sso/callback/", rp.redirect_uri_list())

    def test_the_grant_comes_from_the_row_not_from_the_request(self):
        """A redeemer must not be able to grant itself more than was offered.

        `roles` propagates staff and superuser to the child, and `trusted` skips
        the consent screen. Both are decided at mint time and read from the
        database — this asserts extra keys in the request body change nothing.
        """
        minted = _mint(scopes="openid email", trusted=False)
        grant = redeem({
            **_request(minted.ticket),
            "scopes": "openid email profile roles",
            "trusted": True,
            "granted_scopes": "openid roles",
        })
        self.assertEqual(grant.scopes, "openid email")
        rp = SSORelyingParty.objects.get(pk=minted.relying_party.pk)
        self.assertEqual(rp.allowed_scopes, "openid email")
        self.assertFalse(rp.trusted)


class RedeemRefusalTests(TestCase):
    """Everything that must not work. Each is a way in if it did."""

    def test_a_code_cannot_be_used_twice(self):
        minted = _mint()
        redeem(_request(minted.ticket))
        with self.assertRaises(EnrollmentError) as caught:
            redeem(_request(minted.ticket))
        self.assertEqual(caught.exception.code, "already_redeemed")

    def test_an_expired_code_is_refused(self):
        minted = _mint(ttl=timedelta(minutes=5))
        SSOFederationInvite.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        with self.assertRaises(EnrollmentError) as caught:
            redeem(_request(minted.ticket))
        self.assertEqual(caught.exception.code, "expired")

    def test_a_revoked_code_is_refused(self):
        minted = _mint()
        SSOFederationInvite.objects.update(revoked_at=timezone.now())
        with self.assertRaises(EnrollmentError) as caught:
            redeem(_request(minted.ticket))
        self.assertEqual(caught.exception.code, "revoked")

    def test_a_callback_on_another_host_is_refused(self):
        """The host pin — the whole reason a photographed QR is survivable.

        Without it, whoever holds the code registers THEIR callback as a trusted
        relying party and starts receiving tokens for this platform's users.
        """
        minted = _mint()
        with self.assertRaises(EnrollmentError) as caught:
            redeem(_request(minted.ticket, callback="https://evil.test/sso/callback/"))
        self.assertEqual(caught.exception.code, "host_mismatch")
        # And it names the host, because this is usually a typo not an attack.
        self.assertIn(CONSUMER_HOST, caught.exception.message)

    def test_a_subdomain_does_not_satisfy_the_pin(self):
        minted = _mint()
        with self.assertRaises(EnrollmentError):
            redeem(_request(
                minted.ticket, callback=f"https://evil.{CONSUMER_HOST}/sso/callback/",
            ))

    def test_an_unknown_code_is_refused(self):
        _mint()
        other = wire.encode_ticket(PROVIDER, wire.new_secret())
        with self.assertRaises(EnrollmentError) as caught:
            redeem(_request(other))
        self.assertEqual(caught.exception.code, "invalid_ticket")

    def test_a_missing_callback_is_refused(self):
        minted = _mint()
        with self.assertRaises(EnrollmentError) as caught:
            redeem({"ticket": minted.ticket, "label": "x"})
        self.assertEqual(caught.exception.code, "bad_request")

    def test_a_relative_callback_is_refused(self):
        minted = _mint()
        with self.assertRaises(EnrollmentError) as caught:
            redeem(_request(minted.ticket, callback="/sso/callback/"))
        self.assertEqual(caught.exception.code, "bad_request")

    def test_a_failed_attempt_is_recorded_even_though_it_rolled_back(self):
        """The counter must survive the transaction it failed inside.

        Recording it inside the atomic block would roll it back with everything
        else, so the audit trail would show only successes — exactly backwards
        for a field whose job is to reveal somebody guessing.
        """
        minted = _mint()
        for _ in range(3):
            with self.assertRaises(EnrollmentError):
                redeem(_request(minted.ticket, callback="https://evil.test/x/"))
        row = SSOFederationInvite.objects.get()
        self.assertEqual(row.attempt_count, 3)
        self.assertEqual(row.last_error, "host_mismatch")
        self.assertIsNone(row.redeemed_at)

    def test_a_successful_redemption_records_where_it_came_from(self):
        minted = _mint()
        redeem(_request(minted.ticket), source_ip="203.0.113.9")
        row = SSOFederationInvite.objects.get()
        self.assertEqual(row.redeemed_ip, "203.0.113.9")
        self.assertEqual(row.redeemed_callback_uri, CALLBACK)
        self.assertIsNotNone(row.redeemed_at)


@override_settings(FEDERATION_KEY="a-deployment-key")
class DeploymentBindingTests(TestCase):
    """An invite is only redeemable on the deployment that minted it.

    `.env` and the database have different lifetimes: RESET=1 wipes the database
    while the key survives, and a restored production backup lands on a staging
    box carrying production's pending invites.
    """

    def test_an_invite_from_another_deployment_is_refused(self):
        minted = _mint()
        with override_settings(FEDERATION_KEY="a-different-deployment"):
            with self.assertRaises(EnrollmentError) as caught:
                redeem(_request(minted.ticket))
        self.assertEqual(caught.exception.code, "invalid_ticket")

    def test_the_same_deployment_still_works(self):
        minted = _mint()
        self.assertTrue(redeem(_request(minted.ticket)).client_secret)

    def test_a_host_with_no_key_still_federates(self):
        """Unconfigured must mean "skip the check", not "fail closed"."""
        with override_settings(FEDERATION_KEY=""):
            minted = _mint()
            self.assertTrue(redeem(_request(minted.ticket)).client_secret)


class SecretRotationTests(TestCase):
    """Re-pairing a live federation must not log everyone out."""

    def _paired(self):
        minted = _mint()
        grant = redeem(_request(minted.ticket))
        return SSORelyingParty.objects.get(pk=minted.relying_party.pk), grant.client_secret

    def test_the_old_secret_keeps_working_after_a_rotation(self):
        rp, first_secret = self._paired()
        # The far side proves it holds the first secret, as a real login would.
        rp.secret_proven_at = timezone.now()
        rp.save(update_fields=["secret_proven_at"])

        second_secret = rp.rotate_client_secret()
        rp.save()

        self.assertEqual(rp.check_client_secret(second_secret), "current")
        self.assertEqual(rp.check_client_secret(first_secret), "previous")

    def test_an_unproven_secret_is_not_worth_preserving(self):
        """Two fumbled pairings must not push the last good secret out.

        Without the secret_proven_at guard, the second rotation would move a
        secret nobody ever used into the previous slot and discard the working
        one.
        """
        rp, first_secret = self._paired()
        rp.rotate_client_secret()          # never proven
        third = rp.rotate_client_secret()
        rp.save()
        self.assertEqual(rp.check_client_secret(third), "current")
        self.assertIsNone(rp.check_client_secret(first_secret))

    def test_the_window_closes(self):
        rp, first_secret = self._paired()
        rp.secret_proven_at = timezone.now()
        rp.save(update_fields=["secret_proven_at"])
        rp.rotate_client_secret()
        rp.previous_secret_expires_at = timezone.now() - timedelta(seconds=1)
        rp.save()
        self.assertIsNone(rp.check_client_secret(first_secret))

    def test_re_applying_the_same_secret_does_not_rotate(self):
        """Sidecars re-provision on every container start with the same value.

        Without the short-circuit each restart would open a pointless grace
        window and churn the hash.
        """
        rp, _ = self._paired()
        rp.secret_proven_at = timezone.now()
        rp.save(update_fields=["secret_proven_at"])

        fixed = rp.rotate_client_secret("a-fixed-deployment-secret")
        rp.save()
        hash_before = rp.client_secret_hash
        previous_before = rp.previous_secret_hash
        rotated_before = rp.secret_rotated_at

        # A restart re-provisions with the identical value. Nothing may move —
        # not the hash, and not the previous-secret slot, which would otherwise
        # be churned on every container start.
        again = rp.rotate_client_secret("a-fixed-deployment-secret")

        self.assertEqual(again, fixed)
        self.assertEqual(rp.client_secret_hash, hash_before)
        self.assertEqual(rp.previous_secret_hash, previous_before)
        self.assertEqual(rp.secret_rotated_at, rotated_before)


class ProvisioningIsolationTests(TestCase):
    """Host configuration and pairing must not fight over a row."""

    def test_host_config_refuses_to_overwrite_a_paired_registration(self):
        """Otherwise the next container start silently revokes a live pairing."""
        from toto.sso_master.provisioning import (
            RelyingPartyProvisioningError,
            create_relying_party,
        )

        minted = _mint()
        redeem(_request(minted.ticket))
        rp = SSORelyingParty.objects.get(pk=minted.relying_party.pk)

        with self.assertRaises(RelyingPartyProvisioningError):
            create_relying_party(
                name="Impostor",
                client_id=rp.client_id,
                redirect_uris=["https://evil.test/cb/"],
                force_recreate=True,
            )
        rp.refresh_from_db()
        self.assertNotIn("https://evil.test/cb/", rp.redirect_uri_list())

    def test_a_sidecar_registration_is_untouched_by_pairing(self):
        """Gitea must keep working. Its row is not pairing-managed and stays put."""
        from toto.sso_master.provisioning import create_relying_party

        provisioned = create_relying_party(
            name="Gitea",
            client_id="gitea",
            redirect_uris=["https://provider.test/gitea/cb"],
            raw_secret="the-deployment-secret",
            force_recreate=True,
        )
        gitea = provisioned.relying_party
        self.assertFalse(gitea.pairing_managed)

        _mint(label="Gitea")   # an invite that would have stolen the id

        gitea.refresh_from_db()
        self.assertEqual(gitea.client_id, "gitea")
        self.assertTrue(gitea.verify_client_secret("the-deployment-secret"))


class PKCETests(TestCase):
    """`plain` is refused; S256 still works.

    A public client's challenge travels in the /authorize query string, so a
    "verifier" equal to it is known to anyone who saw that URL. S256 is the only
    method advertised and the only one accepted.
    """

    def test_s256_verifies(self):
        import base64
        import hashlib

        from toto.sso_master.services import verify_pkce

        verifier = "a" * 64
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        self.assertTrue(verify_pkce(verifier, challenge, "S256"))
        self.assertFalse(verify_pkce("wrong" * 13, challenge, "S256"))

    def test_a_missing_method_is_treated_as_s256_not_plain(self):
        """OIDC defaults an absent method to plain, which is the weak one."""
        import base64
        import hashlib

        from toto.sso_master.services import verify_pkce

        verifier = "b" * 64
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        self.assertTrue(verify_pkce(verifier, challenge, None))
        # ...and the plain interpretation must NOT work.
        self.assertFalse(verify_pkce(challenge, challenge, None))

    def test_plain_is_refused(self):
        from toto.sso_master.services import verify_pkce

        self.assertFalse(verify_pkce("same-value", "same-value", "plain"))

    def test_no_challenge_still_means_no_pkce(self):
        """Confidential clients send none and authenticate with a secret."""
        from toto.sso_master.services import verify_pkce

        self.assertTrue(verify_pkce(None, None, None))

    def test_only_s256_is_advertised(self):
        """A client reads the discovery document to decide what to send."""
        from django.test import Client
        from django.urls import reverse

        from toto.sso_core.federation.bridge import provider_urlconf
        from toto.sso_core.federation.fixtures import _ensure_platform

        _ensure_platform()          # get_issuer needs one, or discovery 500s
        with provider_urlconf():
            body = Client(headers={"host": "provider.test"}).get(
                reverse("sso:openid_configuration"),
            ).json()
        self.assertEqual(body["code_challenge_methods_supported"], ["S256"])


class EndToEndPairingTests(TestCase):
    """The whole act of federation, both servers, in one process.

    Everything above tests one side. This drives the consumer's real pairing
    client against the provider's real endpoint through the same loopback the
    token exchange uses — which is the only way to catch the seams, like the two
    sides disagreeing about how a request body is encoded.
    """

    def test_a_consumer_can_pair_and_then_sign_somebody_in(self):
        from toto.sso_client.models import OIDCProviderConfig
        from toto.sso_client.pairing import pair
        from toto.sso_core import vault
        from toto.sso_core.federation.bridge import ProviderLoopback
        from toto.sso_core.federation.fixtures import _ensure_platform

        _ensure_platform()
        minted = _mint()

        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched():
            config = pair(minted.ticket, callback_uri=CALLBACK, label="Consumer")

        # The consumer stored what the provider issued...
        self.assertTrue(config.active)
        self.assertEqual(config.client_id, minted.relying_party.client_id)
        self.assertTrue(config.token_endpoint.endswith("/sso/token/"))

        # ...the secret is in the vault, not in a column...
        self.assertIsNotNone(config.secret_id)
        secret = vault.read_secret(config.secret)
        self.assertTrue(secret)
        blob = " ".join(
            str(getattr(config, f.name, "")) for f in OIDCProviderConfig._meta.fields
        )
        self.assertNotIn(secret, blob)

        # ...and it is the secret the provider will actually accept.
        rp = SSORelyingParty.objects.get(pk=minted.relying_party.pk)
        self.assertEqual(rp.check_client_secret(secret), "current")
        self.assertIn(CALLBACK, rp.redirect_uri_list())

    def test_pairing_refuses_while_the_stale_env_var_is_set(self):
        """It used to silently beat the stored value, so pairing would 'succeed'
        and every login would then fail with invalid_client."""
        import os
        from unittest import mock

        from toto.sso_client.pairing import PairingError, pair

        minted = _mint()
        with mock.patch.dict(os.environ, {"SSO_CLIENT_SECRET": "stale"}):
            with self.assertRaises(PairingError) as caught:
                pair(minted.ticket, callback_uri=CALLBACK)
        self.assertEqual(caught.exception.code, "stale_env")
        # And it refused BEFORE spending the code.
        self.assertTrue(SSOFederationInvite.objects.get().is_redeemable)

    def test_a_provider_refusal_reaches_the_operator_intact(self):
        from toto.sso_client.pairing import PairingError, pair
        from toto.sso_core.federation.bridge import ProviderLoopback

        minted = _mint()
        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched():
            with self.assertRaises(PairingError) as caught:
                pair(minted.ticket, callback_uri="https://wrong.test/sso/callback/")
        # The provider's own sentence, not a status code.
        self.assertIn(CONSUMER_HOST, caught.exception.message)
