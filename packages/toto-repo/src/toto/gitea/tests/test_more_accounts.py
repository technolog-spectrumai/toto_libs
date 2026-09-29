"""Linking a portal user to their forge account — what happens when the forge says no.

`test_client` covers provisioning when everything answers. Linking is where a
half-done job would hurt: a forge user created with no token, a token minted
but not stored, or a failure that reads like "you have no repositories". Each
refusal below must raise `GiteaError` (which the page shows) and leave the
account row in a state the next visit can finish from. The forge is mocked at
`requests`, as the house style has it — no gitea container.
"""

from unittest import mock

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.gitea import client, permissions
from toto.gitea.errors import GiteaError
from toto.gitea.models import GiteaAccount

FORGE = dict(GITEA_ENABLED=True, GITEA_SVC_PASSWORD="svc-pw",
             GITEA_INTERNAL_URL="http://gitea:3000")


def _resp(status, payload=None, text=""):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    r.text = text
    return r


@override_settings(**FORGE)
class LinkingFailureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.com", "pw")

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_forge_that_errors_on_lookup_creates_nothing(self, req):
        req.side_effect = [_resp(500, text="boom")]
        with self.assertRaises(GiteaError) as caught:
            client.ensure_account(self.user)
        self.assertIn("HTTP 500", str(caught.exception))
        self.assertEqual(req.call_count, 1)          # no POST /admin/users after it
        self.assertEqual(GiteaAccount.objects.get(user=self.user).token_encrypted, "")

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_refused_user_creation_is_reported_with_the_forges_words(self, req):
        req.side_effect = [_resp(404), _resp(422, text="email already in use")]
        with self.assertRaises(GiteaError) as caught:
            client.ensure_account(self.user)
        self.assertIn("email already in use", str(caught.exception))

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_refused_token_leaves_the_account_ready_to_try_again(self, req):
        req.side_effect = [_resp(200), _resp(200, payload=[]),
                           _resp(403, text="token scope not allowed")]
        with self.assertRaises(GiteaError):
            client.ensure_account(self.user)
        account = GiteaAccount.objects.get(user=self.user)
        self.assertEqual(account.get_token(), "")
        # The next visit finishes the job: the user exists now, so it goes
        # straight to minting.
        req.side_effect = [_resp(200), _resp(200, payload=[]),
                           _resp(201, payload={"sha1": "tok-2"})]
        self.assertEqual(client.ensure_account(self.user).get_token(), "tok-2")

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_user_without_an_email_is_linked_on_a_noreply_address(self, req):
        nomail = User.objects.create_user("bob", "", "pw")
        req.side_effect = [_resp(404), _resp(201), _resp(200, payload=[]),
                           _resp(201, payload={"sha1": "tok"})]
        client.ensure_account(nomail)
        self.assertEqual(req.call_args_list[1].kwargs["json"]["email"], "bob@noreply.localhost")

    @mock.patch("toto.gitea.client.requests.request")
    def test_the_forge_username_is_the_sanitised_portal_name(self, req):
        odd = User.objects.create_user("ann marie/o'neil", "a@example.com", "pw")
        req.side_effect = [_resp(200), _resp(200, payload=[]),
                           _resp(201, payload={"sha1": "tok"})]
        account = client.ensure_account(odd)
        self.assertEqual(account.username, "ann-marie-o-neil")
        self.assertIn("/users/ann-marie-o-neil", req.call_args_list[0].args[1])

    def test_a_long_name_is_cut_to_forty_characters(self):
        self.assertEqual(len(client.sanitize_username("x" * 80)), 40)

    @mock.patch("toto.gitea.client.requests.get")
    def test_a_failed_repository_list_raises_rather_than_reading_as_empty(self, get):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        account.set_token("tok")
        get.return_value = _resp(401, text="bad token")
        with self.assertRaises(GiteaError):
            client.list_repos(account)

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_repo_creation_cap_is_not_flipped_for_a_user_the_forge_lost(self, req):
        req.side_effect = [_resp(404, text="no such user")]
        with self.assertRaises(GiteaError):
            client.set_max_repo_creation("ghost", 0)
        self.assertEqual(req.call_count, 1)          # no PATCH

    @mock.patch("toto.gitea.client.requests.request")
    def test_a_refused_cap_flip_raises(self, req):
        req.side_effect = [_resp(200, {"login": "alice"}), _resp(422, text="nope")]
        with self.assertRaises(GiteaError):
            client.set_max_repo_creation("alice", 0)

    def test_only_urls_under_the_internal_base_are_ours(self):
        self.assertFalse(client.owns(""))
        self.assertFalse(client.owns("http://gitea:3000"))
        self.assertFalse(client.owns("http://gitea:3000.evil.example/alice/x.git"))
        self.assertTrue(client.owns(client.clone_url("alice", "x")))

    def test_an_account_with_no_token_answers_an_empty_one(self):
        account = GiteaAccount.objects.create(user=self.user, username="alice")
        self.assertEqual(account.get_token(), "")
        self.assertIn("alice", str(account))


class AccessLevelTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("member", password="pw")
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.root = User.objects.create_superuser("root", "r@example.com", "pw")

    def test_nobody_anonymous_uses_the_forge(self):
        for level in ("staff", "superuser", "authenticated"):
            with self.subTest(level=level), override_settings(GITEA_ACCESS=level):
                self.assertFalse(permissions.can_use(AnonymousUser()))
                self.assertFalse(permissions.can_use(None))

    def test_each_level_admits_whom_it_says(self):
        table = {"staff": (False, True, True), "superuser": (False, False, True),
                 "authenticated": (True, True, True)}
        for level, expected in table.items():
            with self.subTest(level=level), override_settings(GITEA_ACCESS=level):
                self.assertEqual(tuple(permissions.can_use(u) for u in
                                       (self.member, self.staff, self.root)), expected)

    @override_settings(GITEA_ACCESS="superuser")
    def test_the_refusal_names_the_setting(self):
        self.assertIn("'superuser'", permissions.refusal())


class PageFailureTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="T", author="A", publication_year=2026, active=True)
        self.user = User.objects.create_user("alice", "alice@example.com", "pw", is_staff=True)
        self.client.force_login(self.user)

    @override_settings(**FORGE)
    def test_a_forge_that_does_not_answer_is_a_sentence_not_a_500(self):
        with mock.patch("toto.gitea.client.ensure_account",
                        side_effect=ConnectionError("refused")):
            response = self.client.get(reverse("gitea:index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"],
                         "Gitea did not answer. It may still be starting up.")
        self.assertEqual(response.context["repos"], [])
        self.assertIsNone(response.context["storage"])

    @override_settings(**{**FORGE, "GITEA_ACCESS": "authenticated"})
    def test_a_member_does_not_see_the_whole_forges_numbers(self):
        member = User.objects.create_user("member", "m@example.com", "pw")
        self.client.force_login(member)
        account = GiteaAccount(user=member, username="member")
        with mock.patch("toto.gitea.client.ensure_account", return_value=account), \
                mock.patch("toto.gitea.client.list_repos", return_value=[]):
            response = self.client.get(reverse("gitea:index"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["forge_sample"])
        self.assertEqual(response.context["storage"]["bytes"], 0)

    @override_settings(**{**FORGE, "GITEA_ACCESS": "authenticated"})
    def test_the_remote_picker_offers_nothing_for_an_account_that_has_no_token(self):
        GiteaAccount.objects.create(user=self.user, username="alice")
        with mock.patch("toto.gitea.client.list_repos") as list_repos:
            data = self.client.get(reverse("gitea:remotes")).json()
        self.assertEqual(data, {"remotes": []})
        list_repos.assert_not_called()

    def test_the_picker_refuses_anything_but_get(self):
        self.assertEqual(self.client.post(reverse("gitea:remotes")).status_code, 405)
