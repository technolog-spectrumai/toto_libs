"""The credential provider — the one thing this app offers toto.repo.

Skipped where toto.repo is absent: the provider has nothing to register with
there, and that must be silence rather than a failure. Everything below
therefore runs only on a host that installs BOTH halves — which zenobia now
does, since it took local git for its Python and LaTeX workspaces. Until then no
host did, and these were written unrun on the principle that a seam nobody
exercises is a seam that has already rotted; they passed first time when a host
finally installed both.
"""

from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from toto.gitea import remotes
from toto.gitea.models import GiteaAccount


class ClaimTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.com", "pw")

    @override_settings(GITEA_ENABLED=True, GITEA_INTERNAL_URL="http://gitea:3000")
    def test_claims_only_this_deployments_urls(self):
        self.assertTrue(remotes._claims("http://gitea:3000/alice/notes.git"))
        self.assertFalse(remotes._claims("https://github.com/alice/notes.git"))

    @override_settings(GITEA_ENABLED=False, GITEA_INTERNAL_URL="http://gitea:3000")
    def test_claims_nothing_when_the_sidecar_is_off(self):
        # Even its own URL. There are no tokens to mint without a sidecar, and
        # claiming would turn a push that would have run verbatim into a
        # provisioning error.
        self.assertFalse(remotes._claims("http://gitea:3000/alice/notes.git"))

    @override_settings(GITEA_ENABLED=True, GITEA_INTERNAL_URL="http://gitea:3000")
    def test_credentials_provision_on_demand(self):
        account = GiteaAccount(user=self.user, username="alice")
        account.set_token("tok-123")
        with mock.patch("toto.gitea.client.ensure_account", return_value=account):
            self.assertEqual(
                remotes._credentials("http://gitea:3000/alice/notes.git", self.user),
                ("alice", "tok-123"))


class RegistrationTests(TestCase):
    def test_the_provider_is_registered_where_toto_repo_exists(self):
        if not django_apps.is_installed("toto.repo"):
            self.skipTest("this host installs the Gitea half only")
        from toto.repo.remotes import registry

        self.assertIn(remotes.PROVIDER_NAME, registry.names())

    @override_settings(GITEA_ENABLED=True, GITEA_INTERNAL_URL="http://gitea:3000")
    def test_toto_repo_routes_our_urls_to_us(self):
        if not django_apps.is_installed("toto.repo"):
            self.skipTest("this host installs the Gitea half only")
        from toto.repo import remotes as repo_remotes

        provider = repo_remotes.registry.provider_for(
            "http://gitea:3000/alice/notes.git")
        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, remotes.PROVIDER_NAME)
        self.assertIsNone(
            repo_remotes.registry.provider_for("https://github.com/a/b.git"))
