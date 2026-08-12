"""The Gitea REST client against mocked HTTP (no gitea container needed)."""

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from toto.gitea import client
from toto.gitea.models import GiteaAccount


def _resp(status, payload=None, text=""):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    r.text = text
    return r


@override_settings(GITEA_ENABLED=True, GITEA_SVC_PASSWORD="svc-pw",
                   GITEA_INTERNAL_URL="http://gitea:3000")
class GiteaClientTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.com", "pw")

    def test_sanitize_username(self):
        self.assertEqual(client.sanitize_username("alice"), "alice")
        self.assertEqual(client.sanitize_username("weird name!"), "weird-name")
        self.assertEqual(client.sanitize_username("...."), "user")

    @mock.patch("toto.gitea.client.requests.request")
    def test_ensure_account_creates_user_and_token(self, req):
        req.side_effect = [
            _resp(404),                                   # GET /users/alice
            _resp(201),                                   # POST /admin/users
            _resp(200, payload=[]),                       # GET tokens
            _resp(201, payload={"sha1": "tok-123"}),      # POST token
        ]
        account = client.ensure_account(self.user)
        self.assertEqual(account.username, "alice")
        self.assertEqual(account.get_token(), "tok-123")
        create_call = req.call_args_list[1]
        self.assertIn("/admin/users", create_call.args[1])
        self.assertEqual(create_call.kwargs["json"]["email"], "alice@example.com")
        self.assertFalse(create_call.kwargs["json"]["must_change_password"])
        # write:user is load-bearing: /user/repos (repo creation) is gated on
        # the `user` scope category, not just write:repository.
        token_call = req.call_args_list[3]
        self.assertEqual(
            token_call.kwargs["json"]["scopes"], ["write:repository", "write:user"]
        )

    @mock.patch("toto.gitea.client.requests.request")
    def test_ensure_account_existing_user_stale_token_replaced(self, req):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        req.side_effect = [
            _resp(200),                                        # GET /users/alice
            _resp(200, payload=[{"name": "portal-repo", "id": 7}]),  # GET tokens
            _resp(204),                                        # DELETE token 7
            _resp(201, payload={"sha1": "tok-new"}),           # POST token
        ]
        account = client.ensure_account(self.user)
        self.assertEqual(account.get_token(), "tok-new")
        delete_call = req.call_args_list[2]
        self.assertIn("/tokens/7", delete_call.args[1])

    @mock.patch("toto.gitea.client.requests.request")
    def test_ensure_account_idempotent_with_token(self, req):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        account.set_token("existing")
        account.save()
        req.side_effect = [_resp(200)]  # only the user lookup
        client.ensure_account(self.user)
        self.assertEqual(req.call_count, 1)

    @mock.patch("toto.gitea.client.requests.post")
    def test_create_repo_collision_suffix(self, post):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        account.set_token("tok")
        account.save()
        post.side_effect = [_resp(409), _resp(201)]
        name = client.create_repo(account, "My Project")
        self.assertEqual(name, "my-project-1")
        self.assertTrue(post.call_args_list[0].kwargs["json"]["private"])

    @mock.patch("toto.gitea.client.requests.get")
    def test_list_repos(self, get):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        account.set_token("tok")
        account.save()
        get.return_value = _resp(200, payload=[
            {"name": "notes", "full_name": "alice/notes", "owner": {"login": "alice"}},
            {"name": "shared", "full_name": "team/shared", "owner": {"login": "team"}},
        ])
        repos = client.list_repos(account)
        self.assertEqual([r["full_name"] for r in repos], ["alice/notes", "team/shared"])
        self.assertEqual(repos[1]["owner"], "team")
        # The clone URL rides along, on the INTERNAL base: the picker's whole
        # job is to fill in an origin, and GITEA_URL is a browser path git
        # cannot resolve.
        self.assertEqual(repos[0]["clone_url"], "http://gitea:3000/alice/notes.git")

    def test_owns_matches_only_this_deployments_urls(self):
        self.assertTrue(client.owns("http://gitea:3000/alice/notes.git"))
        self.assertFalse(client.owns("https://github.com/alice/notes.git"))
        self.assertFalse(client.owns(""))

    def test_token_encrypted_at_rest(self):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        account.set_token("super-secret")
        self.assertNotIn("super-secret", account.token_encrypted)
        self.assertEqual(account.get_token(), "super-secret")

    def test_missing_svc_password(self):
        with override_settings(GITEA_SVC_PASSWORD=""):
            from toto.gitea.errors import GiteaError
            with self.assertRaises(GiteaError):
                client.ensure_account(self.user)
