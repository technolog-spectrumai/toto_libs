"""The chat: a surface like any other, a chip on every page, nothing stored.

Ephemeral is the decision under test. The say action's template has no slot a
transcript could fill; the runs it creates are billed exactly like every
other run; and the only persistence is the usage records those runs already
write.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from . import dispatch, services
from .models import AiProvider, AiRun
from .surfaces import registry
from .vault import vault

User = get_user_model()

PASSPHRASE = "a-test-passphrase-that-is-long-enough"


def _platform():
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def _answer():
    return {"text": "the answer",
            "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                      "total_tokens": 10}, "model": "m"}


class ChatSurfaceTests(TestCase):
    def test_it_is_registered_with_say_and_ask(self):
        surface = registry.get("chat")

        self.assertIsNotNone(surface)
        self.assertIsNotNone(surface.action("say"))
        ask = surface.action("ask")
        self.assertIsNotNone(ask)
        self.assertTrue(ask.needs_instruction)

    def test_nothing_about_it_is_screened_or_kind_noted(self):
        """A chat answer is read, never applied — no file_type, no kind."""
        surface = registry.get("chat")

        self.assertEqual(surface.file_type, "")
        self.assertEqual(surface.kind, "")

    def test_say_carries_the_message_and_nothing_else(self):
        """No history slot exists to fill — single-turn by construction."""
        self.assertEqual(registry.get("chat").action("say").template,
                         "{selection}")


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class ChatAskTests(TestCase):
    def setUp(self):
        _platform()
        vault.clear_cache()
        self.user = User.objects.create_user("chatty", password="pw")
        self.provider = AiProvider.objects.create(label="test", active=True)
        self.provider.secret = vault.store_secret("sk-test", name="chat-key")
        self.provider.save(update_fields=["secret"])

    def _say(self, message="hello there"):
        import json

        return self.client.post(
            reverse("steven:ask"),
            data=json.dumps({"surface": "chat", "action": "say",
                             "selection": message}),
            content_type="application/json")

    def test_a_message_becomes_a_pollable_run(self):
        self.client.force_login(self.user)

        with mock.patch("toto.steven.dispatch.dispatch_run",
                        side_effect=lambda run: run):
            response = self._say()

        self.assertEqual(response.status_code, 200)
        run = AiRun.objects.get()
        self.assertEqual(run.surface, "chat")
        self.assertEqual(run.action, "say")

    def test_anonymous_cannot_chat(self):
        response = self._say()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AiRun.objects.count(), 0)

    def test_an_empty_message_is_refused(self):
        self.client.force_login(self.user)

        response = self._say("   ")

        self.assertEqual(response.status_code, 400)

    def test_a_chat_run_is_billed_like_every_other_run(self):
        from .models import StevenUsageEvent

        run = dispatch.create_run(user=self.user, surface="chat",
                                  action="say", source_text="hello")
        with mock.patch("toto.steven.client.complete",
                        return_value=_answer()):
            services.execute(run)

        keys = set(StevenUsageEvent.objects.values_list("idempotency_key",
                                                        flat=True))
        self.assertEqual(keys, {f"steven.request:{run.pk}",
                                f"steven.tokens:{run.pk}"})

    def test_the_document_mode_sends_document_plus_question(self):
        run = dispatch.create_run(user=self.user, surface="chat",
                                  action="ask", source_text="THE DOCUMENT",
                                  instruction="what is this?")
        with mock.patch("toto.steven.client.complete",
                        return_value=_answer()) as call:
            services.execute(run)

        body = call.call_args.kwargs["messages"][1]["content"]
        self.assertIn("THE DOCUMENT", body)
        self.assertIn("what is this?", body)


class ChipRenderTests(TestCase):
    """The chip reaches pages through the floating-plugin door."""

    def setUp(self):
        _platform()
        self.user = User.objects.create_user("cornered", password="pw")

    def _rendered(self, user):
        from toto.core.plugin import FloatingPlugin

        request = RequestFactory().get("/")
        request.user = user
        return "".join(r.html for r in FloatingPlugin.render_all(request=request))

    def test_an_authenticated_page_carries_the_chip(self):
        html = self._rendered(self.user)

        self.assertIn("stevenChat(", html)
        self.assertIn("steven/chat.js", html)

    def test_an_anonymous_page_does_not(self):
        self.assertNotIn("stevenChat(", self._rendered(AnonymousUser()))
