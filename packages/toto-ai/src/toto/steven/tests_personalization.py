"""The user's standing note: saved on the console, prepended to every call.

Two invariants matter more than the rest: the note can never displace the
action's output rule (it sits ABOVE it, exactly like the operator's voice),
and it is COUNTED — a note rides on every call, so the worst-case wallet
check must see it or the estimate is optimistic for exactly the person who
configured it to be long.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from . import dispatch, services
from .models import AiPersonalization, AiProvider
from .vault import vault

User = get_user_model()

PASSPHRASE = "a-test-passphrase-that-is-long-enough"

NOTE = "Answer in Polish. Keep it under three sentences."


def _platform():
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def _answer():
    return {"text": "ok", "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                    "total_tokens": 2}, "model": "m"}


class ConsoleFormTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("me", password="pw")

    def test_saving_creates_the_row(self):
        self.client.force_login(self.user)

        self.client.post(reverse("steven:personalization"), {"text": NOTE})

        self.assertEqual(AiPersonalization.objects.get(user=self.user).text,
                         NOTE)

    def test_the_note_is_capped(self):
        """It is a bill: it rides on every single call."""
        self.client.force_login(self.user)

        self.client.post(reverse("steven:personalization"),
                         {"text": "x" * 2001})

        row = AiPersonalization.objects.filter(user=self.user).first()
        self.assertTrue(row is None or row.text == "")

    def test_anonymous_cannot_post(self):
        response = self.client.post(reverse("steven:personalization"),
                                    {"text": NOTE})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AiPersonalization.objects.count(), 0)

    def test_the_console_offers_the_form(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("steven:console"))

        self.assertContains(response, "Personalization")

    def test_text_for_answers_empty_for_everything_odd(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(AiPersonalization.text_for(None), "")
        self.assertEqual(AiPersonalization.text_for(AnonymousUser()), "")
        self.assertEqual(AiPersonalization.text_for(self.user), "")


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class PromptAssemblyTests(TestCase):
    """Where the note lands, asserted on a full execute()."""

    def setUp(self):
        _platform()
        vault.clear_cache()
        self.user = User.objects.create_user("noted", password="pw")
        AiPersonalization.objects.create(user=self.user, text=NOTE)
        provider = AiProvider.objects.create(label="test", active=True)
        provider.secret = vault.store_secret("sk-test", name="persona-key")
        provider.save(update_fields=["secret"])
        from .tests import _register_test_surfaces
        _register_test_surfaces()

    def _system_sent(self):
        run = dispatch.create_run(user=self.user, surface="tests",
                                  action="improve", source_text="words")
        with mock.patch("toto.steven.client.complete",
                        return_value=_answer()) as call:
            services.execute(run)
        return call.call_args.kwargs["messages"][0]["content"]

    def test_the_note_reaches_the_system_message(self):
        self.assertIn(NOTE, self._system_sent())

    def test_the_action_rule_still_goes_last(self):
        """With a voice, with only a note, and with neither: whatever else is
        configured, the output rule is the instruction in force."""
        from toto.core.ai_surfaces import AgentVoice, compose_system
        from .tests import TEST_SURFACE

        action = TEST_SURFACE.action("improve")
        voice = AgentVoice(name="Steven", persona="Chatty.")

        both = compose_system(TEST_SURFACE, action, voice,
                              personalization=NOTE)
        note_only = compose_system(TEST_SURFACE, action, None,
                                   personalization=NOTE)
        neither = compose_system(TEST_SURFACE, action, None)

        for system in (both, note_only, neither):
            self.assertTrue(system.endswith(action.system))
        self.assertIn(NOTE, both)
        self.assertIn(NOTE, note_only)
        # And the note sits BELOW the persona — nearer the question.
        self.assertGreater(both.find(NOTE), both.find("Chatty."))

    def test_another_users_note_never_leaks_in(self):
        stranger = User.objects.create_user("other", password="pw")
        AiPersonalization.objects.create(user=stranger,
                                         text="STRANGER SENTINEL")

        self.assertNotIn("STRANGER SENTINEL", self._system_sent())

    def test_the_note_is_counted_in_the_worst_case(self):
        provider = AiProvider.objects.get()
        blank = User.objects.create_user("blank", password="pw")

        with_note = services.worst_case_units(provider, "sel", self.user)
        without = services.worst_case_units(provider, "sel", blank)

        self.assertGreater(with_note, without)
