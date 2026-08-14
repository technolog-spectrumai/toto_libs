"""The settings page: three tabs, and a key that never comes back out.

The constraint under test, verbatim from the spec: credentials must be
encrypted at rest, never displayed again after submission, excluded from
logs, restricted to authorized users, and validated without leaking secrets.
Every class here pins one clause of that sentence to a behaviour.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from . import services
from .client import ProviderError
from .models import AiAgent, AiProvider
from .vault import vault

User = get_user_model()

PASSPHRASE = "a-test-passphrase-that-is-long-enough"
SENTINEL = "sk-SENTINEL-KEY-123"


def _platform():
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class SettingsBase(TestCase):
    def setUp(self):
        _platform()
        vault.clear_cache()
        self.staff = User.objects.create_user("op", password="pw",
                                              is_staff=True)
        self.plain = User.objects.create_user("user", password="pw")


class GateTests(SettingsBase):
    """Restricted to authorized users — 403, never a redirect."""

    def _endpoints(self, pk):
        return [
            ("get", reverse("steven:manage")),
            ("get", reverse("steven:manage") + "?tab=agents"),
            ("get", reverse("steven:manage") + "?tab=statistics"),
            ("get", reverse("steven:provider_new")),
            ("get", reverse("steven:provider_edit", args=[pk])),
            ("post", reverse("steven:provider_activate", args=[pk])),
            ("post", reverse("steven:provider_test", args=[pk])),
            ("get", reverse("steven:agent_new")),
        ]

    def test_an_ordinary_user_gets_403_everywhere(self):
        provider = AiProvider.objects.create(label="p")
        self.client.force_login(self.plain)
        for method, url in self._endpoints(provider.pk):
            with self.subTest(url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 403)

    def test_anonymous_gets_403_everywhere_too(self):
        provider = AiProvider.objects.create(label="p")
        for method, url in self._endpoints(provider.pk):
            with self.subTest(url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 403)

    def test_staff_can_open_all_three_tabs(self):
        self.client.force_login(self.staff)
        for tab in ("connection", "agents", "statistics"):
            with self.subTest(tab=tab):
                response = self.client.get(
                    reverse("steven:manage") + f"?tab={tab}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["tab"], tab)

    def test_a_superuser_who_is_not_staff_still_counts(self):
        """is_superuser does not imply is_staff in Django. Both count —
        the quota desk once got this wrong."""
        root = User.objects.create_user("root", password="pw",
                                        is_superuser=True)
        self.client.force_login(root)
        self.assertEqual(
            self.client.get(reverse("steven:manage")).status_code, 200)

    def test_the_legacy_tab_names_fall_back_to_connection(self):
        """Old ?tab=identity links now live on each agent's own page."""
        self.client.force_login(self.staff)
        response = self.client.get(reverse("steven:manage") + "?tab=identity")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["tab"], "connection")


class KeyDisciplineTests(SettingsBase):
    """Never displayed again after submission — and encrypted at rest."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def _create(self, key=SENTINEL):
        return self.client.post(reverse("steven:provider_new"), {
            "label": "OpenAI", "base_url": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini", "temperature": "0.20",
            "max_output_tokens": "1024", "timeout": "60",
            "new_api_key": key,
        }, follow=True)

    def test_the_key_never_appears_in_any_later_response(self):
        response = self._create()
        self.assertNotIn(SENTINEL.encode(), response.content)

        for url in (reverse("steven:manage"),
                    reverse("steven:provider_edit",
                            args=[AiProvider.objects.get().pk])):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertNotIn(SENTINEL.encode(), page.content)

    def test_the_row_shows_a_status_line_not_a_value(self):
        self._create()
        page = self.client.get(reverse("steven:manage"))
        self.assertContains(page, "set ·")

    def test_the_key_is_encrypted_at_rest_and_round_trips(self):
        self._create()
        provider = AiProvider.objects.get()

        self.assertIsNotNone(provider.secret)
        # Not in the secret row's bytes: the envelope is real, not encoded.
        for value in provider.secret.__dict__.values():
            self.assertNotIn(SENTINEL, str(value))
        self.assertEqual(vault.read_secret(provider.secret), SENTINEL)

    def test_replacing_a_key_repoints_and_retires_the_old_row(self):
        self._create()
        provider = AiProvider.objects.get()
        first = provider.secret

        self.client.post(reverse("steven:provider_edit", args=[provider.pk]), {
            "label": "OpenAI", "base_url": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini", "temperature": "0.20",
            "max_output_tokens": "1024", "timeout": "60",
            "new_api_key": "sk-second",
        })

        provider.refresh_from_db()
        self.assertNotEqual(provider.secret_id, first.pk)
        self.assertEqual(vault.read_secret(provider.secret), "sk-second")

    def test_the_audit_line_exists_and_excludes_the_secret(self):
        """Excluded from logs: the event is recorded, the value is not."""
        from toto.gervazy.models import CryptoAuditLog

        self._create()

        events = CryptoAuditLog.objects.filter(action="set_ai_api_key")
        self.assertTrue(events.exists())
        for event in events:
            self.assertNotIn(SENTINEL, event.reason)

    def test_a_vault_failure_keeps_the_row_and_says_the_key_stands(self):
        from .vault import VaultUnavailable

        self._create()
        provider = AiProvider.objects.get()
        before = provider.secret_id

        with mock.patch.object(services, "store_api_key",
                               side_effect=VaultUnavailable("locked")):
            response = self.client.post(
                reverse("steven:provider_edit", args=[provider.pk]), {
                    "label": "Renamed", "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini", "temperature": "0.20",
                    "max_output_tokens": "1024", "timeout": "60",
                    "new_api_key": "sk-doomed",
                }, follow=True)

        provider.refresh_from_db()
        self.assertContains(response, "NOT changed")
        self.assertEqual(provider.secret_id, before)
        # The row edits themselves still landed — the key is the only casualty.
        self.assertEqual(provider.label, "Renamed")

    def test_the_view_marks_the_key_field_sensitive(self):
        """Kept out of Django's error reports, the same way jess's page is.
        The decorator stamps the REQUEST at call time, so that is where the
        proof lives — on the POST itself, not the redirect that follows it."""
        response = self.client.post(reverse("steven:provider_new"), {
            "label": "OpenAI", "base_url": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini", "temperature": "0.20",
            "max_output_tokens": "1024", "timeout": "60",
            "new_api_key": SENTINEL,
        })

        self.assertEqual(response.wsgi_request.sensitive_post_parameters,
                         ("new_api_key",))


class ProbeTests(SettingsBase):
    """Validated without leaking secrets."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.provider = AiProvider.objects.create(label="probe-me")
        self.provider.secret = vault.store_secret(SENTINEL, name="probe-key")
        self.provider.save(update_fields=["secret"])

    def test_a_working_provider_reports_only_snippet_model_tokens(self):
        answer = {"text": "ok, and here is a very long tail " + "x" * 100,
                  "usage": {"total_tokens": 5}, "model": "gpt-4.1-mini"}
        with mock.patch("toto.steven.client.complete", return_value=answer):
            response = self.client.post(
                reverse("steven:provider_test", args=[self.provider.pk]),
                follow=True)

        self.assertContains(response, "gpt-4.1-mini")
        self.assertNotIn(SENTINEL.encode(), response.content)
        # The 40-character truncation lives in the helper, not the caller.
        self.assertNotIn(b"x" * 60, response.content)

    def test_a_failing_provider_reports_the_refusal_without_the_key(self):
        with mock.patch("toto.steven.client.complete",
                        side_effect=ProviderError("401 said no")):
            response = self.client.post(
                reverse("steven:provider_test", args=[self.provider.pk]),
                follow=True)

        self.assertContains(response, "did not answer")
        self.assertNotIn(SENTINEL.encode(), response.content)

    def test_a_keyless_provider_is_refused_before_any_call(self):
        bare = AiProvider.objects.create(label="bare")
        with mock.patch("toto.steven.client.complete") as call:
            response = self.client.post(
                reverse("steven:provider_test", args=[bare.pk]), follow=True)

        call.assert_not_called()
        self.assertContains(response, "no API key")


class OneActiveTests(SettingsBase):
    """Several rows, one voice — for providers and agents alike."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_activating_a_provider_deactivates_the_other(self):
        first = AiProvider.objects.create(label="first", active=True)
        second = AiProvider.objects.create(label="second")

        self.client.post(reverse("steven:provider_activate", args=[second.pk]))

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(second.active)
        self.assertFalse(first.active)

    def test_activating_an_agent_deactivates_the_other(self):
        first = AiAgent.objects.create(name="First", active=True)
        second = AiAgent.objects.create(name="Second")

        self.client.post(reverse("steven:agent_activate", args=[second.pk]))

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(second.active)
        self.assertFalse(first.active)


class AgentEditTests(SettingsBase):
    """The old two-tab page, instance-bound and explicit about activation."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_a_new_agent_saves_inactive(self):
        """Activation is the list's explicit button now, not a side effect of
        typing a persona."""
        self.client.post(reverse("steven:agent_new"), {
            "form": "prompt", "persona": "You are brief.",
            "language": "", "house_rules": "",
        })

        agent = AiAgent.objects.get()
        self.assertEqual(agent.persona, "You are brief.")
        self.assertFalse(agent.active)

    def test_the_identity_tab_edits_an_existing_row(self):
        agent = AiAgent.objects.create(name="Old")

        self.client.post(reverse("steven:agent_edit", args=[agent.pk]), {
            "form": "identity", "name": "New", "icon": "fa-solid fa-robot",
            "tagline": "", "description": "",
        })

        agent.refresh_from_db()
        self.assertEqual(agent.name, "New")

    def test_the_prompt_tab_edits_the_same_row(self):
        agent = AiAgent.objects.create(name="Voice")

        self.client.post(reverse("steven:agent_edit", args=[agent.pk]), {
            "form": "prompt", "persona": "Precise.", "language": "Polish",
            "house_rules": "Never invent.",
        })

        agent.refresh_from_db()
        self.assertEqual(agent.language, "Polish")
        self.assertEqual(agent.house_rules, "Never invent.")

    def test_the_agents_tab_lists_every_row(self):
        AiAgent.objects.create(name="Alpha", active=True)
        AiAgent.objects.create(name="Beta")

        response = self.client.get(reverse("steven:manage") + "?tab=agents")

        self.assertContains(response, "Alpha")
        self.assertContains(response, "Beta")
