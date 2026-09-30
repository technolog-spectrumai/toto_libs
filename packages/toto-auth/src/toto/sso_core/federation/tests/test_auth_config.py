"""The host-facing API of toto.auth_config.

Pure unit tests, and until now there were none anywhere — despite this being the
function every host calls to decide which auth apps it installs and which url
tree it mounts.
"""
from django.test import SimpleTestCase

from toto.auth_config import (
    MODE_CONSUMER,
    MODE_LOCAL,
    MODE_PROVIDER,
    AuthConfigError,
    auth_apps,
    auth_urlpatterns,
    authentication_backends,
    login_url,
    resolve_auth,
)


def resolve(**env):
    return resolve_auth(env.get)


class ResolveAuthTests(SimpleTestCase):
    def test_provider_is_the_default_mode(self):
        # Every host that predates the strategy split relies on this: no
        # TOTO_AUTH_MODE in the environment must keep meaning "provider".
        self.assertEqual(resolve().mode, MODE_PROVIDER)

    def test_each_mode_resolves(self):
        for mode in (MODE_LOCAL, MODE_PROVIDER, MODE_CONSUMER):
            self.assertEqual(resolve(TOTO_AUTH_MODE=mode).mode, mode)

    def test_an_unknown_mode_is_a_hard_error(self):
        with self.assertRaises(AuthConfigError):
            resolve(TOTO_AUTH_MODE="peer")

    def test_open_registration_argument_overrides_the_flag(self):
        self.assertFalse(resolve_auth({"SSO_OPEN_REGISTRATION": "1"}.get,
                                      open_registration=False).open_registration)
        self.assertTrue(resolve_auth({}.get, open_registration=True).open_registration)

    def test_only_a_literal_one_enables_a_flag(self):
        # Same trap as toto.features.flag: a YAML `true` reads as off.
        self.assertTrue(resolve(SSO_OPEN_REGISTRATION="1").open_registration)
        self.assertFalse(resolve(SSO_OPEN_REGISTRATION="true").open_registration)


class AuthAppsTests(SimpleTestCase):
    def test_provider_installs_the_master(self):
        apps = auth_apps(resolve(TOTO_AUTH_MODE=MODE_PROVIDER))
        self.assertIn("toto.sso_master", apps)
        self.assertNotIn("toto.sso_client", apps)

    def test_consumer_installs_the_client(self):
        apps = auth_apps(resolve(TOTO_AUTH_MODE=MODE_CONSUMER))
        self.assertIn("toto.sso_client", apps)
        self.assertNotIn("toto.sso_master", apps)

    def test_sso_core_rides_along_in_both_federated_modes(self):
        # This is what lets the federation suite ship in sso_core and be
        # inherited by provider and consumer hosts alike.
        for mode in (MODE_PROVIDER, MODE_CONSUMER):
            self.assertIn("toto.sso_core", auth_apps(resolve(TOTO_AUTH_MODE=mode)))

    def test_local_mode_installs_neither_side(self):
        apps = auth_apps(resolve(TOTO_AUTH_MODE=MODE_LOCAL))
        self.assertEqual(apps, ["toto.social_login"])


class AuthUrlpatternsTests(SimpleTestCase):
    def test_every_mode_serves_the_sso_namespace(self):
        # Base templates hard-reverse sso:login / sso:logout, so a mode that
        # dropped the namespace would 500 the shared chrome on every page.
        for mode in (MODE_LOCAL, MODE_PROVIDER, MODE_CONSUMER):
            patterns = auth_urlpatterns(resolve(TOTO_AUTH_MODE=mode))
            self.assertTrue(patterns, f"{mode} mounted nothing")

    def test_the_two_federated_modes_mount_at_different_prefixes(self):
        # sso_master.urls carries its own sso/ and .well-known/ prefixes and is
        # mounted at the root; sso_client.urls is mounted under sso/. A host
        # that hardcodes one shape breaks in the other mode.
        provider = auth_urlpatterns(resolve(TOTO_AUTH_MODE=MODE_PROVIDER))[0]
        consumer = auth_urlpatterns(resolve(TOTO_AUTH_MODE=MODE_CONSUMER))[0]
        self.assertEqual(str(provider.pattern), "")
        self.assertEqual(str(consumer.pattern), "sso/")


class ModeInvariantsTests(SimpleTestCase):
    def test_login_url_is_the_same_name_in_every_mode(self):
        for mode in (MODE_LOCAL, MODE_PROVIDER, MODE_CONSUMER):
            self.assertEqual(login_url(resolve(TOTO_AUTH_MODE=mode)), "sso:login")

    def test_model_backend_serves_every_mode_behind_the_lockout(self):
        # The sign-in lockout (2026-09-30) goes first in every mode: it must
        # be asked before any backend compares a password.
        for mode in (MODE_LOCAL, MODE_PROVIDER, MODE_CONSUMER):
            self.assertEqual(
                authentication_backends(resolve(TOTO_AUTH_MODE=mode)),
                ["toto.core.signin_lockout.SigninLockoutBackend",
                 "django.contrib.auth.backends.ModelBackend"],
            )
