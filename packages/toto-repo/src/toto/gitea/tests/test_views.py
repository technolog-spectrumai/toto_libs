"""The two doors: the page, and the remote picker other apps read.

The picker's contract is the interesting one — it must degrade to an empty list
for every reason there is, because its caller is a convenience beside a text
field that already works. A picker that 500s would block making a purely local
repository over a remote nobody had asked for yet.
"""

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.gitea.models import GiteaAccount


class RemotePickerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.com", "pw",
                                             is_staff=True)
        self.client.force_login(self.user)

    @override_settings(GITEA_ENABLED=False)
    def test_disabled_deployment_offers_nothing(self):
        data = self.client.get(reverse("gitea:remotes")).json()
        self.assertEqual(data, {"remotes": []})

    @override_settings(GITEA_ENABLED=True)
    def test_no_account_yet_offers_nothing(self):
        # Provisioning happens when the user visits the page, not when some
        # other app's modal opens. An empty list is the honest answer.
        data = self.client.get(reverse("gitea:remotes")).json()
        self.assertEqual(data, {"remotes": []})

    @override_settings(GITEA_ENABLED=True, GITEA_INTERNAL_URL="http://gitea:3000")
    def test_repositories_are_offered_as_label_and_url(self):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        account.set_token("tok")
        account.save()
        with mock.patch("toto.gitea.client.list_repos", return_value=[
                {"full_name": "alice/notes", "owner": "alice", "name": "notes",
                 "clone_url": "http://gitea:3000/alice/notes.git"}]):
            data = self.client.get(reverse("gitea:remotes")).json()
        self.assertEqual(data["remotes"], [
            {"label": "alice/notes", "url": "http://gitea:3000/alice/notes.git"}])

    @override_settings(GITEA_ENABLED=True)
    def test_an_unreachable_gitea_is_an_empty_list_not_an_error(self):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        account.set_token("tok")
        account.save()
        with mock.patch("toto.gitea.client.list_repos",
                        side_effect=RuntimeError("connection refused")):
            response = self.client.get(reverse("gitea:remotes"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"remotes": []})

    @override_settings(GITEA_ENABLED=True, GITEA_ACCESS="superuser")
    def test_below_the_gate_offers_nothing(self):
        data = self.client.get(reverse("gitea:remotes")).json()
        self.assertEqual(data, {"remotes": []})

    def test_anonymous_redirected(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("gitea:remotes")).status_code, 302)


class PageTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("alice", "alice@example.com", "pw",
                                             is_staff=True)
        self.client.force_login(self.user)

    @override_settings(GITEA_ENABLED=False)
    def test_without_the_sidecar_the_page_says_so(self):
        # A supported state, not a broken one — the app can be installed on a
        # host whose sidecar is off. An empty repository list would read as
        # "you have none", which is a different and wrong statement.
        response = self.client.get(reverse("gitea:index"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("not running on this deployment", response.content.decode())

    @override_settings(GITEA_ENABLED=True, GITEA_URL="/gitea/")
    def test_the_page_provisions_and_lists(self):
        account = GiteaAccount(user=self.user, username="alice")
        with mock.patch("toto.gitea.client.ensure_account", return_value=account), \
                mock.patch("toto.gitea.client.list_repos", return_value=[
                    {"full_name": "alice/notes", "owner": "alice", "name": "notes",
                     "clone_url": "http://gitea:3000/alice/notes.git"}]):
            response = self.client.get(reverse("gitea:index"))
        body = response.content.decode()
        self.assertIn("alice/notes", body)
        self.assertIn('href="/gitea/alice/notes"', body)

    @override_settings(GITEA_ENABLED=True)
    def test_a_provisioning_failure_is_shown_not_raised(self):
        from toto.gitea.errors import GiteaError

        with mock.patch("toto.gitea.client.ensure_account",
                        side_effect=GiteaError("GITEA_SVC_PASSWORD is not configured")):
            response = self.client.get(reverse("gitea:index"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("GITEA_SVC_PASSWORD", response.content.decode())

    @override_settings(GITEA_ENABLED=True, GITEA_ACCESS="superuser")
    def test_below_the_gate_is_refused(self):
        self.assertEqual(self.client.get(reverse("gitea:index")).status_code, 403)
