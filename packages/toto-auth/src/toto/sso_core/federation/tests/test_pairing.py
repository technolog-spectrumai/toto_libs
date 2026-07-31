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
        "". If this ever fails, check that the upscale and border are still applied
        in qr.render_data_uri.

        The secret is FIXED, not random, on purpose. cv2's QRCodeDetector is
        heuristic and cannot read roughly one payload in a few hundred of its OWN
        encoder's output — no amount of upscaling or thresholding recovers those
        specific images (measured). A random secret here therefore flakes the gate
        about that often. Production never depends on this: the QR always sits next
        to a copy-paste text field, so an unreadable image just means the operator
        pastes the code. This asserts the render→read plumbing on an image cv2 can
        read; the flake is a property of the library, not our code.
        """
        import base64

        ticket = wire.encode_ticket(PROVIDER, bytes(range(32)))
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

    def test_a_callback_whose_host_is_ambiguous_is_refused(self):
        """Defence in depth on the pin: refuse a callback urlparse and an HTTP
        client would read as different hosts, rather than pin against one of them.

        'https://consumer.test\\@evil.test' is host consumer.test to urlparse — so
        it would satisfy a pin for consumer.test — but an HTTP client connects to
        evil.test. A real consumer's callback (built from get_host()) can carry
        neither userinfo nor a backslash, so this is always hostile.
        """
        minted = _mint()
        for bad in (
            f"https://{CONSUMER_HOST}\\@evil.test/sso/callback/",
            f"https://user@{CONSUMER_HOST}/sso/callback/",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(EnrollmentError) as caught:
                    redeem(_request(minted.ticket, callback=bad))
                self.assertEqual(caught.exception.code, "bad_request")
                # Refused before the code is spent, so a fixed callback can retry.
                self.assertTrue(SSOFederationInvite.objects.get().is_redeemable)

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


class AdminPageTests(TestCase):
    """The admin pages must render, and must not leak.

    Nothing else covers them: the boot smoke does not sign in, and a 500 on the
    invite page would only be found by an operator trying to use the feature.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.test import Client

        from toto.sso_core.federation.fixtures import _ensure_platform

        _ensure_platform()
        User = get_user_model()
        self.admin = User.objects.create_superuser("root", "root@x.test", "pw")
        self.client = Client(headers={"host": "provider.test"})
        self.client.force_login(self.admin)

    def test_the_invite_page_renders_and_mints(self):
        from django.urls import reverse

        from toto.sso_core.federation.bridge import provider_urlconf

        with provider_urlconf():
            url = reverse("admin:sso_master_ssorelyingparty_invite")
            self.assertEqual(self.client.get(url).status_code, 200)

            response = self.client.post(url, {
                "expected_host": CONSUMER_HOST,
                "trusted": "on",
                "ttl_minutes": "5",
            })
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        invite = SSOFederationInvite.objects.get()
        self.assertEqual(invite.expected_host, CONSUMER_HOST)
        # roles was not ticked, so it must not have been granted.
        self.assertNotIn("roles", invite.granted_scopes)
        # The QR and the copyable code are both on the page.
        self.assertIn("data:image/png;base64,", body)
        self.assertIn(invite.ticket_prefix, body)

    def test_the_invite_form_defaults_to_the_advertised_five_minutes(self):
        """Every operator-facing text promises five minutes; the form must agree.

        The form always passes an explicit ttl to mint(), so the model's
        DEFAULT_INVITE_TTL never reaches that path — the default has to be the
        one the form pre-fills, or a defaults-minted code is dead in 60 seconds
        while the consumer-side operator is told they have five minutes.
        """
        from django.urls import reverse

        from toto.sso_master.models import DEFAULT_INVITE_TTL_MINUTES
        from toto.sso_core.federation.bridge import provider_urlconf

        self.assertEqual(DEFAULT_INVITE_TTL_MINUTES, 5)
        with provider_urlconf():
            url = reverse("admin:sso_master_ssorelyingparty_invite")
            body = self.client.get(url).content.decode()
            # The number input is pre-filled with five, not the one-minute floor.
            self.assertIn('name="ttl_minutes"', body)
            self.assertIn('value="5"', body)

            # And a submission that leaves the field blank still mints five minutes.
            self.client.post(url, {"expected_host": CONSUMER_HOST, "trusted": "on",
                                   "ttl_minutes": ""})
        invite = SSOFederationInvite.objects.latest("id")
        window = (invite.expires_at - invite.created_at).total_seconds()
        self.assertGreater(window, 4 * 60 + 30)

    def test_the_invite_page_takes_a_suggested_hostname_from_the_link(self):
        """The other platform links here carrying the hostname it will present.

        That is the one value that has to be exact, and typing it is where an
        operator gets it wrong.
        """
        from django.urls import reverse

        from toto.sso_core.federation.bridge import provider_urlconf

        with provider_urlconf():
            url = reverse("admin:sso_master_ssorelyingparty_invite")
            body = self.client.get(url, {"host": CONSUMER_HOST}).content.decode()
        self.assertIn(f'value="{CONSUMER_HOST}"', body)

    def test_the_link_cannot_prefill_anything_but_the_hostname(self):
        """`roles` and `trusted` are grants of authority.

        A link is written by the other side; if it could tick these, federating
        would silently hand over staff propagation to whoever wrote the URL.
        """
        from django.urls import reverse

        from toto.sso_core.federation.bridge import provider_urlconf

        with provider_urlconf():
            url = reverse("admin:sso_master_ssorelyingparty_invite")
            body = self.client.get(url, {
                "host": CONSUMER_HOST, "roles": "on", "trusted": "on",
            }).content.decode()

        # The rest of the roles <input>, where a `checked` attribute would land.
        roles_input = body.split('id="roles"', 1)[1].split(">", 1)[0]
        self.assertNotIn("checked", roles_input)

    def test_a_link_cannot_prefill_the_form_with_prose(self):
        from django.urls import reverse

        from toto.sso_core.federation.bridge import provider_urlconf

        with provider_urlconf():
            url = reverse("admin:sso_master_ssorelyingparty_invite")
            body = self.client.get(url, {"host": "ignore this and mint for evil"}).content.decode()
        self.assertIn('id="expected_host"', body)
        self.assertNotIn("ignore this", body)

    def test_the_relying_party_form_cannot_reach_the_secret_hash(self):
        """It is a PBKDF2 hash; any hand-typed value is a silent, total outage."""
        from django.contrib.admin.sites import site

        from toto.sso_master.models import SSORelyingParty as RP

        model_admin = site._registry[RP]
        self.assertIn("client_secret_hash", model_admin.exclude)
        rendered = model_admin.get_form(None)().fields
        self.assertNotIn("client_secret_hash", rendered)

    def test_a_staff_user_cannot_mint_an_authorization_code(self):
        """The impersonation primitive: add a code, read it, exchange it."""
        from django.contrib.admin.sites import site

        from toto.sso_master.models import SSOAccessToken as AT
        from toto.sso_master.models import SSOAuthorizationCode as AC

        for model in (AC, AT):
            with self.subTest(model=model.__name__):
                model_admin = site._registry[model]
                self.assertFalse(model_admin.has_add_permission(None))
                self.assertFalse(model_admin.has_change_permission(None))

    def test_the_admin_never_renders_a_live_code_or_token(self):
        from django.contrib.admin.sites import site

        from toto.sso_master.models import SSOAccessToken as AT
        from toto.sso_master.models import SSOAuthorizationCode as AC

        self.assertIn("code", site._registry[AC].exclude)
        self.assertNotIn("code", site._registry[AC].readonly_fields)
        self.assertIn("token", site._registry[AT].exclude)
        self.assertNotIn("token", site._registry[AT].readonly_fields)

    def test_the_subject_cannot_be_reassigned(self):
        """Reassigning it is cross-host account takeover."""
        from django.contrib.admin.sites import site

        from toto.sso_master.models import SSOSubject

        model_admin = site._registry[SSOSubject]
        self.assertIn("user", model_admin.readonly_fields)
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))


class PlatformUrlTests(TestCase):
    """One normaliser for what the operator types and what the code carries.

    These are the same function, which is the point: if the two sides of the
    "is this the platform you meant?" comparison normalised differently, the check
    would refuse correct pairings and the operator would learn to distrust it.
    """

    def test_it_canonicalises_the_ways_people_write_an_address(self):
        from toto.sso_client.pairing import platform_url

        for written in (
            "zenobia.test",
            "https://zenobia.test",
            "https://zenobia.test/",
            "HTTPS://Zenobia.TEST",
            "https://zenobia.test:443",
            "https://zenobia.test/admin/sso_master/ssorelyingparty/",
            "  https://zenobia.test  ",
        ):
            with self.subTest(written=written):
                self.assertEqual(platform_url(written), "https://zenobia.test")

    def test_a_bare_hostname_is_assumed_secure(self):
        """Guessing http:// would produce an address the provider must refuse."""
        from toto.sso_client.pairing import platform_url

        self.assertEqual(platform_url("zenobia.test"), "https://zenobia.test")

    def test_a_real_port_survives(self):
        from toto.sso_client.pairing import platform_url

        self.assertEqual(
            platform_url("https://zenobia.test:8443"), "https://zenobia.test:8443",
        )

    def test_it_refuses_what_is_not_an_address(self):
        from toto.sso_client.pairing import PairingError, platform_url

        # "not a url" is here because urlparse accepts it: it reports the whole
        # phrase as the hostname, so without a shape check the page presented
        # prose back to the operator as a real destination.
        for junk in (
            "", "   ", "ftp://zenobia.test", "https://", "https://h:notaport",
            "not a url", "https://zenobia .test", "https://-nope.test",
        ):
            with self.subTest(junk=junk):
                with self.assertRaises(PairingError) as caught:
                    platform_url(junk)
                self.assertEqual(caught.exception.code, "bad_url")

    def test_it_refuses_a_host_an_http_client_reads_differently(self):
        """urlparse and requests/urllib3 disagree on userinfo and backslash URLs.

        urlparse reads 'https://evil.test\\@good.test' as host good.test (the part
        before the backslash is userinfo to it), while requests connects to
        evil.test. If platform_url canonicalised such an input to good.test, the
        host this function returns — used both to compare against the operator's
        declared platform and to build the POST endpoint — would not be the host
        actually contacted, and the whole "which platform?" check would be a
        no-op. So these are refused outright.
        """
        from toto.sso_client.pairing import PairingError, platform_url

        for hostile in (
            "https://evil.test\\@good.test",
            "https://user@good.test",
            "https://user:pass@good.test",
            "https://good.test\\evil.test",
        ):
            with self.subTest(hostile=hostile):
                with self.assertRaises(PairingError) as caught:
                    platform_url(hostile)
                self.assertEqual(caught.exception.code, "bad_url")


class WrongPlatformTests(TestCase):
    """A pairing code names its own provider, so the operator must name it too.

    Without the declared destination, a code that arrived from the wrong place is
    redeemed against whatever server it names — this host federates to a stranger
    and finds out afterwards.
    """

    def test_a_code_for_another_platform_is_refused(self):
        from toto.sso_client.pairing import PairingError, pair

        minted = _mint()
        with self.assertRaises(PairingError) as caught:
            pair(
                minted.ticket,
                callback_uri=CALLBACK,
                expect_url="https://somewhere-else.test",
            )
        self.assertEqual(caught.exception.code, "wrong_platform")
        # Both addresses are named, because "wrong platform" without saying which
        # leaves the operator with nothing to check.
        self.assertIn("https://provider.test", caught.exception.message)
        self.assertIn("https://somewhere-else.test", caught.exception.message)

    def test_nothing_is_sent_anywhere_when_it_is_refused(self):
        """The refusal has to happen on the way OUT.

        Transmitting the code to the host it names is itself the disclosure, so
        checking after the call would defeat the purpose of checking at all.
        """
        from toto.sso_core.federation.bridge import ProviderLoopback
        from toto.sso_client.pairing import PairingError, pair

        minted = _mint()
        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched():
            with self.assertRaises(PairingError):
                pair(minted.ticket, callback_uri=CALLBACK,
                     expect_url="https://somewhere-else.test")

        self.assertEqual(loopback.calls, [])
        self.assertTrue(SSOFederationInvite.objects.get().is_redeemable)

    def test_a_code_whose_address_lies_to_the_check_is_refused_before_the_call(self):
        """The host-differential exploit.

        A pairing code carries its own provider address, decoded with no URL
        validation. An attacker mints one whose address is
        'https://evil.test\\@good.test': urlparse (and so the check) reads the host
        as good.test, but requests/urllib3 connect to evil.test. If pair() built
        the endpoint from the raw address, an operator naming the trusted good.test
        would pass the check and yet hand the exchange to evil.test, which could
        return a grant of its choosing and take over every federated login. pair()
        must canonicalise the address through the SAME function the check uses and
        refuse anything that does not survive it — before any network call, and
        whether or not a destination was declared.
        """
        from toto.sso_core.federation.bridge import ProviderLoopback
        from toto.sso_client.pairing import PairingError, pair

        crafted = wire.encode_ticket("https://evil.test\\@good.test", wire.new_secret())

        # Even with the operator naming the host the address pretends to be.
        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched():
            with self.assertRaises(PairingError) as caught:
                pair(crafted, callback_uri=CALLBACK, expect_url="https://good.test")
        self.assertEqual(caught.exception.code, "bad_ticket")
        self.assertEqual(loopback.calls, [])

        # And with no declared destination at all: the canonicalisation is not
        # gated on expect_url, so the malformed address is refused regardless.
        with self.assertRaises(PairingError) as caught:
            pair(crafted, callback_uri=CALLBACK)
        self.assertEqual(caught.exception.code, "bad_ticket")

    def test_the_declared_platform_does_not_have_to_be_written_identically(self):
        from toto.sso_core.federation.bridge import ProviderLoopback
        from toto.sso_client.pairing import pair
        from toto.sso_core.federation.fixtures import _ensure_platform

        _ensure_platform()
        minted = _mint()
        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched():
            # Typed without a scheme and with a trailing slash; same platform.
            config = pair(minted.ticket, callback_uri=CALLBACK,
                          expect_url="provider.test/")
        self.assertTrue(config.active)

    def test_the_matching_platform_still_pairs(self):
        from toto.sso_core.federation.bridge import ProviderLoopback
        from toto.sso_client.pairing import pair
        from toto.sso_core.federation.fixtures import _ensure_platform

        _ensure_platform()
        minted = _mint()
        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched():
            config = pair(minted.ticket, callback_uri=CALLBACK, expect_url=PROVIDER)
        self.assertTrue(config.active)


class GuidedFederationPageTests(TestCase):
    """The consumer's two-step "Federate to a platform" page.

    The steps are the feature: step one is where the operator says where they are
    going, which is what turns "redeem whatever this code names" into a claim this
    host can check.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.test import Client

        from toto.sso_core.federation.fixtures import _ensure_platform

        _ensure_platform()
        User = get_user_model()
        self.admin = User.objects.create_superuser("root", "root@x.test", "pw")
        self.staff = User.objects.create_user(
            "clerk", "clerk@x.test", "pw", is_staff=True,
        )
        self.client = Client(headers={"host": CONSUMER_HOST})
        self.client.force_login(self.admin)

    @property
    def url(self):
        from django.urls import reverse

        return reverse("admin:sso_client_oidcproviderconfig_federate")

    def test_step_one_asks_where_to_federate_to(self):
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Federate to", body)
        # And it has not asked for a code yet — that is the whole point of two
        # steps; a page that shows both at once teaches the operator to paste
        # first and read afterwards.
        self.assertNotIn('name="code"', body)

    def test_step_two_names_the_platform_and_asks_for_its_code(self):
        response = self.client.post(
            self.url, {"target": "provider.test"}, secure=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("https://provider.test", body)
        self.assertIn('name="code"', body)
        # The link that lands the other administrator on the right page, carrying
        # the one value that has to be exact.
        self.assertIn(
            "https://provider.test/admin/sso_master/ssorelyingparty/invite/"
            f"?host={CONSUMER_HOST}",
            body,
        )

    def test_step_one_refuses_an_address_that_is_not_one(self):
        response = self.client.post(self.url, {"target": "not a url"}, secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("not a platform address", body)
        self.assertNotIn('name="code"', body)

    def test_step_one_refuses_plain_http_before_it_can_waste_a_code(self):
        """/sso/enroll/ refuses plaintext, so this address can never work."""
        with override_settings(DEBUG=False):
            response = self.client.post(
                self.url, {"target": "http://provider.test"}, secure=True,
            )
        self.assertIn("not secure", response.content.decode())
        self.assertNotIn('name="code"', response.content.decode())

    def test_pasting_a_code_for_another_platform_is_refused_by_the_page(self):
        minted = _mint()
        response = self.client.post(self.url, {
            "target": "https://somewhere-else.test",
            "action": "pair",
            "code": minted.ticket,
        }, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("but you asked to federate to", response.content.decode())
        self.assertTrue(SSOFederationInvite.objects.get().is_redeemable)

    def test_the_whole_page_federates_end_to_end(self):
        from toto.sso_client.models import OIDCProviderConfig
        from toto.sso_core.federation.bridge import ProviderLoopback

        minted = _mint()
        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched():
            response = self.client.post(self.url, {
                "target": "provider.test",
                "action": "pair",
                "code": minted.ticket,
                "label": "Head office",
            }, secure=True)

        self.assertEqual(response.status_code, 302)
        config = OIDCProviderConfig.objects.get()
        self.assertTrue(config.active)
        self.assertEqual(config.portal_url, PROVIDER)
        self.assertIsNotNone(config.secret_id)

    def test_an_uploaded_qr_photograph_is_the_same_as_pasting(self):
        # The point is that the view routes an uploaded file through qr.read and then
        # pairs. qr.read is stubbed to the ticket rather than gambling on cv2 decoding a
        # random rendered image — cv2 cannot read ~0.4% of its own encoder's output (see
        # QRTests), which would flake the gate. The real render→read path is covered
        # deterministically by QRTests.test_a_rendered_qr_can_be_read_back.
        from unittest import mock

        from django.core.files.uploadedfile import SimpleUploadedFile

        from toto.sso_client.models import OIDCProviderConfig
        from toto.sso_core.federation.bridge import ProviderLoopback

        minted = _mint()
        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched(), mock.patch("toto.sso_core.qr.read", return_value=minted.ticket):
            response = self.client.post(self.url, {
                "target": "provider.test",
                "action": "pair",
                "code": "",
                "image": SimpleUploadedFile("qr.png", b"a-photo-of-a-qr", "image/png"),
            }, secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(OIDCProviderConfig.objects.get().active)

    def test_a_fresh_upload_beats_a_stale_pasted_code(self):
        """A failed attempt re-fills the textarea, so a later QR upload must win.

        Otherwise the operator, told to fetch a fresh code and handed a QR photo,
        uploads it while the dead code still sits in the box — and the dead code
        is silently used again.

        qr.read is stubbed (see the sibling upload test for why): the point here is
        precedence — the decoded upload beats the stale textarea — not cv2's decode.
        """
        from unittest import mock

        from django.core.files.uploadedfile import SimpleUploadedFile

        from toto.sso_client.models import OIDCProviderConfig
        from toto.sso_core.federation.bridge import ProviderLoopback

        minted = _mint()
        loopback = ProviderLoopback(PROVIDER)
        with loopback.patched(), mock.patch("toto.sso_core.qr.read", return_value=minted.ticket):
            response = self.client.post(self.url, {
                "target": "provider.test",
                "action": "pair",
                "code": "STALE-DEAD-CODE-STILL-IN-THE-BOX",
                "image": SimpleUploadedFile("qr.png", b"a-photo-of-a-qr", "image/png"),
            }, secure=True)

        # The upload was used, not the stale text — so pairing succeeded.
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OIDCProviderConfig.objects.get().active)

    def test_the_connection_name_survives_a_step_two_error(self):
        """target and code are echoed back on a failed step 2; the name must be too.

        Otherwise the operator retypes the code after a failure and submits with a
        silently-emptied name, and the connection is named after the wrong host.
        """
        minted = _mint()
        response = self.client.post(self.url, {
            "target": "https://somewhere-else.test",   # forces a step-2 refusal
            "action": "pair",
            "code": minted.ticket,
            "label": "Head office",
        }, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('value="Head office"', response.content.decode())

    def test_renewing_an_existing_connection_asks_for_a_re_pairing_code(self):
        """A renewal must go through re-pair, not a fresh invite, or accounts orphan."""
        from toto.sso_client.models import OIDCProviderConfig

        OIDCProviderConfig.objects.create(
            label="Head office", portal_url=PROVIDER, client_id="consumer",
            active=True,
        )
        response = self.client.post(self.url, {"target": "provider.test"}, secure=True)
        body = response.content.decode()
        self.assertIn("re-pairing code", body)
        # It must NOT tell them to add a fresh invite, which would strand accounts.
        self.assertNotIn("Invite a platform", body)

    def test_federating_to_a_different_platform_asks_for_a_plain_invite(self):
        from toto.sso_client.models import OIDCProviderConfig

        OIDCProviderConfig.objects.create(
            label="Head office", portal_url="https://elsewhere.test",
            client_id="consumer", active=True,
        )
        response = self.client.post(self.url, {"target": "provider.test"}, secure=True)
        body = response.content.decode()
        self.assertIn("Invite a platform", body)
        self.assertNotIn("re-pairing code", body)

    def test_a_staff_user_without_rights_cannot_federate_the_host(self):
        """admin_view() checks is_staff, not per-model permissions."""
        self.client.force_login(self.staff)
        response = self.client.post(
            self.url, {"target": "provider.test", "action": "pair", "code": "x"},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/", response["Location"])
        self.assertFalse(SSOFederationInvite.objects.filter(redeemed_at__isnull=False))

    def test_an_anonymous_visitor_gets_nowhere(self):
        self.client.logout()
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
