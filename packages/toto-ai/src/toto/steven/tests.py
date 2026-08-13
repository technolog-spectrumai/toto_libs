"""What the assistant promises, asserted rather than hoped.

Three of these matter more than the rest:

* the API key is never in a row, a form or an audit line;
* a **failed** call charges nothing, and a successful one charges the REAL
  token count rather than an estimate;
* nothing is applied to a document without somebody accepting it.

**The vault is real here, not mocked.** Argon2id, the four-tier gervazy
envelope, an actual strongbox — ``jess/tests.py:6-9`` records why, and the same
policy holds: mocking it would leave the one question that matters unanswered.

**No live API call, ever.** ``client.complete`` is patched in every test that
gets that far, so a normal run makes no paid request. ``sabbia/tests.py:348``
gates its live test on ``OPENAI_API_KEY`` being present for the same reason.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from . import dispatch, services
from .client import ProviderError
from .models import AiProvider, AiRun, RunStatus
from toto.core.ai_surfaces import Action, AiSurface, registry
from .vault import VaultUnavailable, vault

User = get_user_model()

#: Any non-empty value works — the point is that the envelope is real.
PASSPHRASE = "a-test-passphrase-that-is-long-enough"

TEST_SURFACE = AiSurface(
    key="tests", label="Tests", kind="prose",
    actions=(
        Action("improve", "Improve", system="s", template="Improve:\n{selection}"),
        Action("translate", "Translate", system="s", needs_instruction=True,
               template="Into {instruction}:\n{selection}"),
    ),
)
HTML_SURFACE = AiSurface(
    key="tests-html", label="Tests HTML", kind="prose", file_type="html",
    actions=(Action("improve", "Improve", system="s", template="{selection}"),),
)


def _register_test_surfaces():
    registry.register(TEST_SURFACE)
    registry.register(HTML_SURFACE)


def _platform():
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def _answer(text="better words", *, prompt=10, completion=20):
    return {
        "text": text,
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion},
        "model": "gpt-4.1-mini",
    }


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class VaultTests(TestCase):
    """The key goes in and never comes back out anywhere it should not."""

    def setUp(self):
        vault.clear_cache()

    def test_it_round_trips_through_a_real_envelope(self):
        secret = vault.store_secret("sk-not-a-real-key", name="k1",
                                    purpose="ai_api_key")

        self.assertEqual(vault.read_secret(secret), "sk-not-a-real-key")

    def test_the_plaintext_is_not_in_the_stored_row(self):
        """Every field, stringified. The one assertion that cannot be fooled."""
        secret = vault.store_secret("sk-plaintext-canary", name="k2")

        blob = " ".join(str(getattr(secret, f.name, ""))
                        for f in secret._meta.fields)
        self.assertNotIn("sk-plaintext-canary", blob)

    def test_the_service_account_cannot_be_logged_into(self):
        vault.store_secret("x", name="k3")

        owner = User.objects.get(username="steven-vault")
        self.assertFalse(owner.is_active)
        self.assertFalse(owner.has_usable_password())

    def test_a_missing_passphrase_is_a_named_refusal_not_a_crash(self):
        with override_settings(STEVEN_VAULT_PASSWORD=""):
            vault.clear_cache()
            with self.assertRaises(VaultUnavailable) as caught:
                vault.open_session()

        self.assertIn("STEVEN_VAULT_PASSWORD", str(caught.exception))

    def test_a_wrong_passphrase_surfaces_as_vault_unavailable(self):
        """A wrong passphrase builds a happy session and fails on the first
        unwrap — so the failure has to be translated where it happens."""
        secret = vault.store_secret("x", name="k4")

        with override_settings(STEVEN_VAULT_PASSWORD="a-different-passphrase"):
            vault.clear_cache()
            with self.assertRaises(VaultUnavailable):
                vault.read_secret(secret)

    def test_the_box_is_steven_own_and_not_sabbia_s(self):
        """One blast radius per secret domain."""
        vault.store_secret("x", name="k5")

        from toto.gervazy.models import UserStrongbox

        self.assertTrue(UserStrongbox.objects.filter(name="steven-system").exists())
        self.assertFalse(UserStrongbox.objects.filter(name="sabbia-system").exists())

    def test_rotating_the_passphrase_keeps_every_secret_readable(self):
        secret = vault.store_secret("sk-survives", name="k6")

        vault.rotate_passphrase(PASSPHRASE, "a-brand-new-passphrase-entirely")

        with override_settings(STEVEN_VAULT_PASSWORD="a-brand-new-passphrase-entirely"):
            vault.clear_cache()
            self.assertEqual(vault.read_secret(secret), "sk-survives")


class ProviderTests(TestCase):
    def test_exactly_one_row_is_active(self):
        first = AiProvider.objects.create(label="one", active=True)
        second = AiProvider.objects.create(label="two", active=True)

        first.refresh_from_db()
        self.assertFalse(first.active)
        self.assertTrue(second.active)
        self.assertEqual(AiProvider.current(), second)

    def test_switching_away_leaves_the_old_row_to_switch_back_to(self):
        first = AiProvider.objects.create(label="one", active=True)
        AiProvider.objects.create(label="two", active=True)

        first.active = True
        first.save()

        self.assertEqual(AiProvider.current(), first)
        self.assertEqual(AiProvider.objects.count(), 2)

    def test_a_provider_with_no_key_is_not_usable(self):
        provider = AiProvider.objects.create(label="keyless", active=True)

        self.assertFalse(provider.is_usable)
        with self.assertRaises(services.NotConfigured):
            services.active_provider()


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class ExecuteTests(TestCase):
    """One run, start to finish, with the network patched out."""

    def setUp(self):
        _platform()
        _register_test_surfaces()
        vault.clear_cache()
        self.user = User.objects.create_user("asker", password="pw")
        self.provider = AiProvider.objects.create(
            label="test", active=True, max_output_tokens=100)
        self.provider.secret = vault.store_secret("sk-test", name="run-key")
        self.provider.save(update_fields=["secret"])

    def _run(self, action="improve", text="some words", instruction=""):
        return dispatch.create_run(user=self.user, surface="tests", action=action,
                                   source_text=text, instruction=instruction)

    def test_a_successful_call_records_the_real_token_count(self):
        run = self._run()

        with mock.patch("toto.steven.client.complete", return_value=_answer()):
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertEqual(run.result, "better words")
        self.assertEqual(run.total_tokens, 30)
        self.assertEqual(run.billable_units, Decimal("0.030"))

    def test_the_selection_is_the_only_thing_sent(self):
        """The billing model rests on this: no document ever goes over the wire."""
        run = self._run(text="just this sentence")

        with mock.patch("toto.steven.client.complete",
                        return_value=_answer()) as call:
            services.execute(run)

        messages = call.call_args.kwargs["messages"]
        body = " ".join(m["content"] for m in messages)
        self.assertIn("just this sentence", body)
        # Two messages, no history: an action is a single shot. The parked app
        # resent an unbounded conversation every turn.
        self.assertEqual(len(messages), 2)

    def test_a_failed_call_charges_nothing(self):
        """The whole benefit of charging afterwards — there is nothing to unwind."""
        from .models import StevenUsageEvent

        run = self._run()

        with mock.patch("toto.steven.client.complete",
                        side_effect=ProviderError("the provider said no")):
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("the provider said no", run.error)
        self.assertEqual(StevenUsageEvent.objects.count(), 0)

    def test_a_successful_call_records_two_usage_events(self):
        from .models import StevenUsageEvent

        run = self._run()

        with mock.patch("toto.steven.client.complete", return_value=_answer()):
            services.execute(run)

        codes = set(StevenUsageEvent.objects.values_list("metric_code", flat=True))
        self.assertEqual(codes, {"ai.request", "ai.tokens_1k"})

    def test_settling_twice_records_one_month_of_events(self):
        """Idempotency keyed on the run's pk, like every metered app here."""
        from .models import StevenUsageEvent

        run = self._run()
        with mock.patch("toto.steven.client.complete", return_value=_answer()):
            services.execute(run)

        services.settle(run)

        self.assertEqual(StevenUsageEvent.objects.count(), 2)

    def test_a_provider_with_no_usage_block_charges_for_the_request_only(self):
        """Guessing a token count in order to bill it would be inventing a number."""
        from .models import StevenUsageEvent

        run = self._run()
        answer = _answer()
        answer["usage"] = {}

        with mock.patch("toto.steven.client.complete", return_value=answer):
            services.execute(run)

        codes = list(StevenUsageEvent.objects.values_list("metric_code", flat=True))
        self.assertEqual(codes, ["ai.request"])

    def test_a_locked_vault_fails_the_run_and_says_which_variable(self):
        run = self._run()

        with override_settings(STEVEN_VAULT_PASSWORD=""):
            vault.clear_cache()
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("STEVEN_VAULT_PASSWORD", run.error)

    def test_a_refused_answer_is_never_offered(self):
        """A completion is untrusted third-party content.

        What is asserted here is STEVEN's half of the deal — that a refusal
        fails the run, empties the result and charges nothing. Whether a given
        string is hostile is `toto.antivirus`'s judgement and is tested there;
        patching the verdict keeps this test about one thing, and keeps it
        meaningful on a host where antivirus is not installed (there the façade
        answers clean-and-unscanned, which is the documented degradation and not
        something this module decides).
        """
        from toto.vault.scanning import Verdict
        from .models import StevenUsageEvent

        run = dispatch.create_run(user=self.user, surface="tests-html",
                                  action="improve", source_text="hello")
        hostile = _answer("<p>hi</p><script>alert(1)</script>")

        with mock.patch("toto.steven.client.complete", return_value=hostile), \
             mock.patch("toto.vault.scanning.scan",
                        return_value=Verdict.refused("active-content", "<script>")):
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("content scanner", run.error)
        self.assertEqual(run.result, "")
        self.assertEqual(StevenUsageEvent.objects.count(), 0)

    def test_a_clean_html_answer_is_offered(self):
        run = dispatch.create_run(user=self.user, surface="tests-html",
                                  action="improve", source_text="hello")

        with mock.patch("toto.steven.client.complete",
                        return_value=_answer("<p>much better</p>")):
            services.execute(run)

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)

    def test_an_answer_bound_for_no_file_type_is_not_screened_at_all(self):
        """The `tests` surface has no file_type: nothing it returns becomes part
        of a rendered document, so there is nothing for a scanner to judge."""
        run = self._run()

        with mock.patch("toto.steven.client.complete",
                        return_value=_answer("<script>fine here</script>")), \
             mock.patch("toto.vault.scanning.scan") as scan:
            services.execute(run)

        scan.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)

    def test_an_unknown_action_fails_the_run_rather_than_calling_out(self):
        run = self._run(action="nonsense")

        with mock.patch("toto.steven.client.complete") as call:
            services.execute(run)

        call.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)


class WorstCaseTests(TestCase):
    def test_the_estimate_is_an_over_estimate(self):
        """Erring high can only refuse somebody very close to empty; erring low
        would let a call run that cannot be paid for."""
        provider = AiProvider(max_output_tokens=1000)

        units = services.worst_case_units(provider, "x" * 4000)

        # 4000 chars ≈ 1000 prompt tokens, plus the 1000-token answer ceiling.
        self.assertEqual(units, Decimal("2.000"))

    def test_an_empty_selection_still_reserves_the_answer_ceiling(self):
        provider = AiProvider(max_output_tokens=500)

        self.assertEqual(services.worst_case_units(provider, ""), Decimal("0.500"))


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class AskViewTests(TestCase):
    def setUp(self):
        _platform()
        _register_test_surfaces()
        vault.clear_cache()
        self.user = User.objects.create_user("web", password="pw")
        self.client.force_login(self.user)
        self.provider = AiProvider.objects.create(label="test", active=True)
        self.provider.secret = vault.store_secret("sk-test", name="view-key")
        self.provider.save(update_fields=["secret"])

    def _ask(self, **payload):
        import json

        body = {"surface": "tests", "action": "improve",
                "selection": "some words"}
        body.update(payload)
        return self.client.post(reverse("steven:ask"), data=json.dumps(body),
                                content_type="application/json")

    def test_an_empty_selection_is_refused_before_anything_is_queued(self):
        response = self._ask(selection="   ")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(AiRun.objects.count(), 0)

    def test_an_enormous_selection_is_refused_with_its_size(self):
        response = self._ask(selection="x" * 20_001)

        self.assertEqual(response.status_code, 400)
        self.assertIn("20001", response.json()["error"])

    def test_an_action_needing_an_instruction_says_so(self):
        response = self._ask(action="translate")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(AiRun.objects.count(), 0)

    def test_an_unknown_surface_is_refused(self):
        self.assertEqual(self._ask(surface="nope").status_code, 400)

    def test_no_worker_is_a_503_that_names_the_flag(self):
        """A refusal an operator can act on beats a timeout nobody can diagnose."""
        with mock.patch("toto.steven.dispatch.workflows_installed",
                        return_value=False):
            response = self._ask()

        self.assertEqual(response.status_code, 503)
        self.assertIn("BUILD_WORKFLOWS", response.json()["error"])
        # The row exists and is closed, so the console shows what happened.
        run = AiRun.objects.get()
        self.assertEqual(run.status, RunStatus.FAILED)

    def test_an_unconfigured_platform_answers_503_not_400(self):
        """The user did nothing wrong, and retrying works the moment an
        operator switches a provider on."""
        AiProvider.objects.update(active=False)

        response = self._ask()

        self.assertEqual(response.status_code, 503)

    def test_a_queued_ask_returns_a_run_to_poll(self):
        with mock.patch("toto.steven.dispatch.dispatch_run",
                        side_effect=lambda run: run):
            response = self._ask()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run_id"], AiRun.objects.get().pk)

    def test_a_stranger_cannot_poll_your_run(self):
        run = dispatch.create_run(user=self.user, surface="tests",
                                  action="improve", source_text="x")
        stranger = User.objects.create_user("stranger", password="pw")
        self.client.force_login(stranger)

        response = self.client.get(reverse("steven:run_status", args=[run.pk]))

        self.assertEqual(response.status_code, 404)

    def test_the_actions_endpoint_describes_a_surface(self):
        response = self.client.get(
            reverse("steven:surface_actions", args=["tests"]))

        keys = {a["key"] for a in response.json()["actions"]}
        self.assertEqual(keys, {"improve", "translate"})

    def test_the_console_renders_and_says_whether_it_is_configured(self):
        response = self.client.get(reverse("steven:console"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["configured"])

    def test_the_console_is_honest_when_nothing_is_set_up(self):
        AiProvider.objects.update(active=False)

        response = self.client.get(reverse("steven:console"))

        self.assertFalse(response.context["configured"])
        self.assertContains(response, "Not configured on this server")


class FailRunTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("closer", password="pw")

    def test_closing_a_run_is_idempotent(self):
        run = dispatch.create_run(user=self.user, surface="tests",
                                  action="improve", source_text="x")

        dispatch.fail_run(run, "first")
        dispatch.fail_run(run, "second")

        run.refresh_from_db()
        self.assertEqual(run.error, "first")

    def test_a_finished_run_is_never_reopened(self):
        run = dispatch.create_run(user=self.user, surface="tests",
                                  action="improve", source_text="x")
        run.finish(status=RunStatus.SUCCESS, result="done")

        dispatch.fail_run(run, "too late")

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)


class SurfaceSeamTests(TestCase):
    """`toto.core.assistant` — the one thing an editor is allowed to know."""

    def test_it_answers_the_key_when_everything_is_in_place(self):
        from toto.core import assistant

        _register_test_surfaces()
        self.assertEqual(assistant.surface_for("tests"), "tests")

    def test_an_unregistered_surface_answers_nothing(self):
        """An editor that has not declared one gets no button, not a crash."""
        from toto.core import assistant

        self.assertEqual(assistant.surface_for("no-such-editor"), "")

    def test_an_uninstalled_assistant_answers_nothing(self):
        """The degradation the whole façade exists for: a host without toto-ai
        renders every editor exactly as it did before."""
        from toto.core import assistant

        with mock.patch("django.apps.apps.is_installed", return_value=False):
            self.assertEqual(assistant.surface_for("tests"), "")

    def test_an_unmounted_app_answers_nothing(self):
        """Installed-but-unmounted is a real state here — zenobia keeps
        toto.mandragora installed for an FK and serves it at no URL."""
        from django.urls import NoReverseMatch

        from toto.core import assistant

        _register_test_surfaces()
        with mock.patch("django.urls.reverse", side_effect=NoReverseMatch):
            self.assertEqual(assistant.surface_for("tests"), "")


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class EditorButtonTests(TestCase):
    """The button appears where a surface exists, and nowhere else.

    Every test here is about an app that may not be installed — the surfaces
    come from `toto.editor` and `toto.cyprian`, which are flag-gated and live in
    two different wheels. Skipping is the honest answer: asserting a surface for
    an absent editor would be asserting a promise nothing keeps.
    """

    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.user = User.objects.create_user("writer", password="pw")

    def setUp(self):
        self.client.force_login(self.user)

    def _require(self, app_label):
        from django.apps import apps

        if not apps.is_installed(app_label):
            self.skipTest(f"{app_label} is not installed on this host")

    def test_the_surface_comes_from_the_file_type_not_the_view(self):
        """`.py` has no view of its own and opens through `text_display`, so a
        class-level answer would offer PROSE actions on Python."""
        self._require("toto.editor")
        from toto.editor.views import BaseFileDisplayView, TextFileDisplayView

        view = TextFileDisplayView()
        for file_type, expected in (("python", "editor-code"),
                                    ("latex", "editor-latex"),
                                    ("html", "editor-markup"),
                                    ("text", "editor-text")):
            with self.subTest(file_type=file_type):
                stub = type("F", (), {"file_type": file_type})()
                self.assertEqual(view.resolve_steven_surface(stub), expected)
        self.assertIn("python", BaseFileDisplayView.STEVEN_SURFACE_BY_TYPE)

    def test_an_html_answer_is_screened_and_a_text_one_is_not(self):
        self._require("toto.editor")
        from toto.core.ai_surfaces import registry

        self.assertEqual(registry.get("editor-markup").file_type, "html")
        self.assertEqual(registry.get("editor-text").file_type, "")

    def test_a_plain_text_file_is_not_screened_as_markup(self):
        """A paragraph that merely MENTIONS <script> is not a threat, and
        refusing it would teach people to ignore the real alarm."""
        self._require("toto.editor")
        from toto.core.ai_surfaces import registry

        self.assertEqual(registry.get("editor-text").file_type, "")
        self.assertEqual(registry.get("editor-code").file_type, "")

    def test_the_svg_editor_offers_nothing(self):
        self._require("toto.editor")
        """SVG has its own editor with its own source view; a prose assistant
        on raw drawing markup is the wrong tool in the wrong place."""
        from toto.editor.views import SvgFileDisplayView

        self.assertEqual(SvgFileDisplayView.steven_surface, "")

    def test_cyprian_declares_a_screened_prose_surface(self):
        """Skipped where cyprian is not installed — placidia pins no toto-works,
        and a surface for an absent editor would be a promise nothing keeps."""
        from django.apps import apps

        from toto.core.ai_surfaces import registry

        self._require("toto.cyprian")

        surface = registry.get("cyprian")
        self.assertIsNotNone(surface)
        self.assertEqual(surface.file_type, "html")
        self.assertIn("improve", {a.key for a in surface.actions})


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE,
                   MEDIA_ROOT=__import__("tempfile").mkdtemp(prefix="steven-wand-"))
class FileWandTests(TestCase):
    """The whole-file action, and the access rule it borrows."""

    @classmethod
    def setUpTestData(cls):
        from toto.vault.models import Bucket

        _platform()
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.stranger = User.objects.create_user("stranger", password="pw")
        cls.bucket = Bucket.objects.create(name="Mine", slug="mine",
                                           owner=cls.owner)

    def setUp(self):
        vault.clear_cache()
        provider = AiProvider.objects.create(label="test", active=True)
        provider.secret = vault.store_secret("sk-test", name="wand-key")
        provider.save(update_fields=["secret"])
        self.file = self._file()

    def _file(self, body=b"the whole document", file_type="text"):
        from django.core.files.base import ContentFile

        from toto.vault.models import VaultFile

        vault_file = VaultFile(owner=self.owner, title="notes.txt",
                               file_type=file_type, bucket=self.bucket)
        vault_file.file.save("notes.txt", ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def _url(self):
        return reverse("steven:file_ask", args=[self.file.pk])

    def test_a_stranger_cannot_ask_about_your_file(self):
        """It borrows the vault's rule rather than inventing a second one."""
        self.client.force_login(self.stranger)

        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_the_owner_gets_the_page(self):
        self.client.force_login(self.owner)

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "notes.txt")

    def test_asking_sends_the_file_and_starts_a_run(self):
        self.client.force_login(self.owner)

        with mock.patch("toto.steven.dispatch.dispatch_run",
                        side_effect=lambda run: run):
            response = self.client.post(self._url(), {"action": "summarise"})

        self.assertEqual(response.status_code, 200)
        run = AiRun.objects.get()
        self.assertEqual(run.surface, "file")
        self.assertEqual(run.source_text, "the whole document")

    def test_a_long_file_is_truncated_and_says_so(self):
        """Refusing would make the feature useless on exactly the documents
        somebody most wants summarised; truncating in silence would let the
        answer describe a document nobody sent."""
        from toto.steven.views import MAX_FILE_CHARS

        self.file = self._file(body=b"x" * (MAX_FILE_CHARS + 500))
        self.client.force_login(self.owner)

        with mock.patch("toto.steven.dispatch.dispatch_run",
                        side_effect=lambda run: run):
            response = self.client.post(self._url(), {"action": "summarise"})

        self.assertTrue(response.json()["truncated"])
        self.assertEqual(len(AiRun.objects.get().source_text), MAX_FILE_CHARS)

    def test_an_action_needing_a_question_says_so(self):
        self.client.force_login(self.owner)

        response = self.client.post(self._url(), {"action": "ask"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(AiRun.objects.count(), 0)

    def test_an_encrypted_file_is_not_readable_at_all(self):
        self.file.is_encrypted = True
        self.file.save(update_fields=["is_encrypted"])
        self.client.force_login(self.owner)

        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_the_wand_is_registered_as_a_builder_service(self):
        """Builder-backed on purpose: a non-builder plugin needs
        FileServiceRun, which lives in a wheel zenobia does not pin."""
        from toto.vault.plugins import FileServicePlugin

        plugin = FileServicePlugin.get("steven")
        self.assertIsNotNone(plugin)
        self.assertTrue(plugin.builder)
        self.assertTrue(plugin.accepts(self.file))

    def test_it_does_not_offer_itself_for_a_video(self):
        """A prompt over bytes nobody can read is a lie in a menu."""
        from toto.vault.plugins import FileServicePlugin

        video = self._file(body=b"\x00\x00", file_type="video")
        self.assertFalse(FileServicePlugin.get("steven").accepts(video))

    def test_the_vault_lists_it_without_the_media_wheel(self):
        """The point of moving the registry: zenobia pins no toto-media-ops."""
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("vault:file_services", args=[self.file.pk]))

        keys = {s["key"] for s in response.json()["services"]}
        self.assertIn("steven", keys)

    def test_a_stranger_cannot_list_the_services_for_your_file(self):
        self.client.force_login(self.stranger)

        response = self.client.get(
            reverse("vault:file_services", args=[self.file.pk]))

        self.assertEqual(response.status_code, 404)


class AllSurfacesTests(TestCase):
    """Every editor's declaration, and the rules that differ between them."""

    def _surface(self, key, app_label):
        from django.apps import apps

        from toto.core.ai_surfaces import registry

        if not apps.is_installed(app_label):
            self.skipTest(f"{app_label} is not installed on this host")
        surface = registry.get(key)
        self.assertIsNotNone(surface, f"{key} was not registered")
        return surface

    def test_memo_is_screened_because_a_block_is_html(self):
        self.assertEqual(self._surface("memo", "toto.memo").file_type, "html")

    def test_primula_has_its_own_vocabulary(self):
        """A range is not prose. "Improve this text" over a column of numbers
        is the wrong question, so primula declares its own actions rather than
        reusing the shared ones and making them mean less everywhere."""
        surface = self._surface("primula", "toto.primula")

        keys = {a.key for a in surface.actions}
        self.assertIn("formula", keys)
        self.assertNotIn("shorten", keys)

    def test_primula_is_not_screened_as_a_workbook(self):
        """What comes back is a formula somebody pastes into a cell — it never
        becomes the file, so screening it as one checks the wrong thing."""
        self.assertEqual(self._surface("primula", "toto.primula").file_type, "")

    def test_notebooks_get_python_code_actions(self):
        surface = self._surface("mandragora", "toto.mandragora")

        self.assertEqual(surface.kind, "code")
        self.assertIn("docstring", {a.key for a in surface.actions})

    def _declared_surfaces(self):
        """The surfaces real apps declared — not this module's fixtures.

        **The registry is a process global**, and every class here that calls
        ``_register_test_surfaces`` leaves TEST_SURFACE and HTML_SURFACE in it
        for the rest of the run. Which classes have run first depends on
        alphabetical ordering, so a new test class can silently change what
        these two see. Filtering by key is what makes them assert a contract
        about editors rather than about test ordering.
        """
        from toto.core.ai_surfaces import registry

        fixtures = {TEST_SURFACE.key, HTML_SURFACE.key}
        return [s for s in registry.all() if s.key not in fixtures]

    def test_every_surface_can_be_asked_a_question(self):
        """The side panel needs ONE action it can count on, whatever editor it
        is docked to."""
        for surface in self._declared_surfaces():
            with self.subTest(surface=surface.key):
                self.assertIsNotNone(surface.action("ask"),
                                     f"{surface.key} has no 'ask' action")

    def test_an_action_that_needs_a_question_says_so(self):
        for surface in self._declared_surfaces():
            action = surface.action("ask")
            with self.subTest(surface=surface.key):
                self.assertTrue(action.needs_instruction)


class DrawerTests(TestCase):
    """The side panel, and the block that used to hide it."""

    def test_it_is_registered_as_a_floating_plugin(self):
        from toto.core.plugin import FloatingPlugin

        self.assertIn("steven_drawer", FloatingPlugin.registry)

    def test_it_is_hidden_from_anonymous_visitors(self):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from toto.steven.plugins.floating_plugins import StevenDrawerPlugin

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        self.assertFalse(StevenDrawerPlugin().visible_for_request(request))

    def test_no_editor_still_blanks_the_floating_block(self):
        """Four editors used to render nothing there, which silenced every
        floating plugin — including the gas pump, so the apps that actually
        charge were the only ones never saying what they cost."""
        import pathlib

        # Source trees only. build/, site-packages and the test venv all hold
        # stale copies of the same templates, and a fix that has not been
        # reinstalled would fail this for the wrong reason.
        packages = pathlib.Path(__file__).resolve().parents[4]
        skip = ("/build/", "/limbo/", "site-packages", ".venv", "/dist/")
        blank = "{" + "% block floating_widgets %" + "}{" + "% endblock %" + "}"

        offenders = [
            str(template)
            for template in packages.rglob("*/src/toto/**/*.html")
            if not any(part in str(template) for part in skip)
            and blank in template.read_text(errors="ignore")
        ]
        self.assertEqual(offenders, [])


class ComposeSystemTests(TestCase):
    """Where an operator's words land relative to the action's own rule."""

    def _voice(self, **kwargs):
        from toto.core.ai_surfaces import AgentVoice

        return AgentVoice(**kwargs)

    def _action(self):
        return Action("improve", "Improve", system="RETURN ONLY THE TEXT.",
                      template="{selection}")

    def test_no_voice_is_the_untouched_action_prompt(self):
        """Every host until somebody opens the management page."""
        from toto.core.ai_surfaces import compose_system

        self.assertEqual(compose_system(TEST_SURFACE, self._action()),
                         "RETURN ONLY THE TEXT.")

    def test_the_action_rule_is_always_last(self):
        """The load-bearing one. Accept pastes the answer straight into a
        document, so no amount of tuning may displace 'return only the text'
        from the position closest to the question."""
        from toto.core.ai_surfaces import compose_system

        system = compose_system(TEST_SURFACE, self._action(), self._voice(
            name="Steven", persona="You are chatty and love preambles.",
            house_rules="Always greet the user warmly first."))

        self.assertTrue(system.rstrip().endswith("RETURN ONLY THE TEXT."))

    def test_a_bare_name_becomes_a_sentence(self):
        from toto.core.ai_surfaces import compose_system

        system = compose_system(TEST_SURFACE, self._action(),
                                self._voice(name="Steven"))

        self.assertIn("You are Steven.", system)

    def test_a_persona_that_already_names_it_is_not_prefixed_twice(self):
        from toto.core.ai_surfaces import compose_system

        system = compose_system(TEST_SURFACE, self._action(), self._voice(
            name="Steven", persona="You are Steven, the house editor."))

        self.assertEqual(system.count("Steven"), 1)

    def test_a_blank_language_says_nothing_about_language(self):
        """The right default on a platform whose users write in several."""
        from toto.core.ai_surfaces import compose_system

        system = compose_system(TEST_SURFACE, self._action(), self._voice())

        self.assertNotIn("Always answer in", system)

    def test_a_note_reaches_only_its_own_kind(self):
        """Tuning LaTeX must not change prose."""
        from toto.core.ai_surfaces import compose_system

        voice = self._voice(kind_notes={"code": "Prefer f-strings."})
        prose = compose_system(TEST_SURFACE, self._action(), voice)
        code = compose_system(
            AiSurface(key="c", label="C", kind="code",
                      actions=(self._action(),)), self._action(), voice)

        self.assertNotIn("f-strings", prose)
        self.assertIn("f-strings", code)

    def test_build_messages_carries_the_voice_into_the_system_message(self):
        from toto.core.ai_surfaces import build_messages

        messages = build_messages(TEST_SURFACE, self._action(),
                                  selection="hello",
                                  voice=self._voice(persona="You are terse."))

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("You are terse.", messages[0]["content"])


class AgentTests(TestCase):
    def test_exactly_one_row_is_active(self):
        from .models import AiAgent

        first = AiAgent.objects.create(name="one", active=True)
        second = AiAgent.objects.create(name="two", active=True)

        first.refresh_from_db()
        self.assertFalse(first.active)
        self.assertEqual(AiAgent.current(), second)

    def test_no_agent_means_no_voice(self):
        from .models import AiAgent

        self.assertIsNone(AiAgent.voice())

    def test_an_inactive_agent_is_not_the_voice(self):
        from .models import AiAgent

        AiAgent.objects.create(name="draft", persona="unused", active=False)

        self.assertIsNone(AiAgent.voice())

    def test_a_corrupt_kind_notes_column_degrades_to_no_notes(self):
        """A hand-edited JSON column must not take the assistant down."""
        from .models import AiAgent

        agent = AiAgent.objects.create(name="x", active=True)
        AiAgent.objects.filter(pk=agent.pk).update(kind_notes=["not", "a", "map"])

        self.assertEqual(AiAgent.voice().kind_notes, {})


@override_settings(STEVEN_VAULT_PASSWORD=PASSPHRASE)
class VoiceInRunTests(TestCase):
    """The configured voice actually reaches the wire."""

    def setUp(self):
        _platform()
        _register_test_surfaces()
        vault.clear_cache()
        self.user = User.objects.create_user("voiced", password="pw")
        self.provider = AiProvider.objects.create(
            label="test", active=True, max_output_tokens=100)
        self.provider.secret = vault.store_secret("sk-test", name="voice-key")
        self.provider.save(update_fields=["secret"])

    def test_the_persona_is_sent_with_every_call(self):
        from .models import AiAgent

        AiAgent.objects.create(name="Steven", active=True,
                               persona="You are the house editor.",
                               house_rules="Never invent a citation.")
        run = dispatch.create_run(user=self.user, surface="tests",
                                  action="improve", source_text="words")

        with mock.patch("toto.steven.client.complete",
                        return_value=_answer()) as call:
            services.execute(run)

        system = call.call_args.kwargs["messages"][0]["content"]
        self.assertIn("You are the house editor.", system)
        self.assertIn("Never invent a citation.", system)

    def test_editing_the_persona_changes_the_next_call_with_no_restart(self):
        """Read per call, never cached."""
        from .models import AiAgent

        agent = AiAgent.objects.create(name="Steven", active=True,
                                       persona="First voice.")
        with mock.patch("toto.steven.client.complete", return_value=_answer()):
            services.execute(dispatch.create_run(
                user=self.user, surface="tests", action="improve",
                source_text="a"))

        agent.persona = "Second voice."
        agent.save()

        with mock.patch("toto.steven.client.complete",
                        return_value=_answer()) as call:
            services.execute(dispatch.create_run(
                user=self.user, surface="tests", action="improve",
                source_text="b"))

        self.assertIn("Second voice.",
                      call.call_args.kwargs["messages"][0]["content"])

    def test_the_worst_case_counts_the_configured_system_prompt(self):
        """It used to be a code constant of known size. Since the management
        page it is whatever somebody typed, and leaving it out would make the
        wallet check optimistic exactly when it was configured to be expensive."""
        from .models import AiAgent

        provider = AiProvider(max_output_tokens=100)
        before = services.worst_case_units(provider, "x" * 400)

        AiAgent.objects.create(name="Steven", active=True,
                               persona="p" * 4000)

        self.assertGreater(services.worst_case_units(provider, "x" * 400), before)

    def test_only_the_longest_note_is_reserved_not_all_of_them(self):
        """Exactly one kind applies to any call; summing five would refuse
        people over tokens that will never be sent."""
        from .models import AiAgent

        AiAgent.objects.create(name="Steven", active=True, kind_notes={
            "prose": "p" * 400, "code": "c" * 400, "latex": "l" * 400})
        provider = AiProvider(max_output_tokens=100)

        units = services.worst_case_units(provider, "")

        # 400 chars ≈ 100 tokens for ONE note, plus "Steven" (6), plus the
        # 100-token ceiling. Three notes would be 300 tokens over.
        self.assertLess(units, Decimal("0.250"))


class ManageViewTests(TestCase):
    """The two tabs, and who may open them."""

    def setUp(self):
        _platform()
        _register_test_surfaces()
        self.url = reverse("steven:manage")
        self.operator = User.objects.create_user("boss", password="pw",
                                                 is_staff=True)
        self.user = User.objects.create_user("nobody", password="pw")

    def test_a_normal_user_gets_403_not_a_redirect(self):
        """A 302 to LOGIN_URL is what jess/views.py exists to avoid."""
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_an_anonymous_visitor_gets_the_same_403(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_a_superuser_who_is_not_staff_may_still_open_it(self):
        """is_superuser does not imply is_staff in Django."""
        root = User.objects.create_user("root", password="pw", is_superuser=True)
        self.client.force_login(root)

        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_it_opens_on_the_identity_tab(self):
        self.client.force_login(self.operator)

        response = self.client.get(self.url)

        self.assertEqual(response.context["tab"], "identity")

    def test_saving_the_identity_creates_the_agent_switched_on(self):
        from .models import AiAgent

        self.client.force_login(self.operator)

        self.client.post(self.url, {"form": "identity", "name": "Ada",
                                    "icon": "fa-solid fa-robot", "active": "on"})

        agent = AiAgent.current()
        self.assertEqual(agent.name, "Ada")
        self.assertEqual(agent.icon, "fa-solid fa-robot")

    def test_a_first_save_from_the_prompt_tab_also_switches_it_on(self):
        """Somebody who types a persona and sees nothing change would be the
        worse default."""
        from .models import AiAgent

        self.client.force_login(self.operator)

        self.client.post(self.url, {"form": "prompt",
                                    "persona": "You are terse."})

        self.assertIsNotNone(AiAgent.current())
        self.assertEqual(AiAgent.current().persona, "You are terse.")

    def test_the_two_tabs_edit_the_same_row(self):
        from .models import AiAgent

        self.client.force_login(self.operator)

        self.client.post(self.url, {"form": "identity", "name": "Ada",
                                    "icon": "fa-solid fa-robot", "active": "on"})
        self.client.post(self.url, {"form": "prompt", "persona": "Terse."})

        self.assertEqual(AiAgent.objects.count(), 1)
        agent = AiAgent.current()
        self.assertEqual((agent.name, agent.persona), ("Ada", "Terse."))

    def test_a_blank_name_is_refused_rather_than_saved(self):
        """'You are .' at the top of every system message, and an empty panel."""
        from .models import AiAgent

        self.client.force_login(self.operator)

        response = self.client.post(self.url, {"form": "identity", "name": " ",
                                               "icon": "fa-solid fa-robot"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AiAgent.objects.count(), 0)

    def test_a_junk_icon_is_refused(self):
        self.client.force_login(self.operator)

        response = self.client.post(self.url, {
            "form": "identity", "name": "Ada", "icon": 'x" onload="alert(1)'})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["identity_form"].is_valid())

    def test_the_note_fields_are_built_from_the_registered_surfaces(self):
        """A host gets note boxes for the editors it installed, not five boxes
        describing editors nobody can open."""
        self.client.force_login(self.operator)

        form = self.client.get(f"{self.url}?tab=prompt").context["prompt_form"]

        kinds = {kind for kind, _where in form.kinds}
        self.assertIn("prose", kinds)
        self.assertEqual(kinds, {s.kind for s in registry.all() if s.kind})

    def test_emptying_a_note_removes_it_rather_than_leaving_it_stored(self):
        from .models import AiAgent

        self.client.force_login(self.operator)
        self.client.post(self.url, {"form": "prompt", "persona": "p",
                                    "note__prose": "Be brief."})
        self.assertEqual(AiAgent.current().kind_notes, {"prose": "Be brief."})

        self.client.post(self.url, {"form": "prompt", "persona": "p",
                                    "note__prose": ""})

        self.assertEqual(AiAgent.current().kind_notes, {})

    def test_the_preview_shows_the_assembled_system_message(self):
        """The point of the second tab: text boxes that build a prompt nobody
        can read is how the parked app shipped one nobody had looked at."""
        self.client.force_login(self.operator)
        self.client.post(self.url, {"form": "prompt",
                                    "persona": "You are the house editor."})

        response = self.client.get(f"{self.url}?tab=prompt")

        systems = " ".join(row["system"] for row in response.context["preview"])
        self.assertIn("You are the house editor.", systems)

    def test_the_page_does_not_edit_the_provider(self):
        """An API key and a persona are edited by different people with
        different care."""
        self.client.force_login(self.operator)
        provider = AiProvider.objects.create(label="live", model="gpt-4.1-mini",
                                             active=True)

        self.client.post(self.url, {"form": "identity", "name": "Ada",
                                    "icon": "fa-solid fa-robot",
                                    "model": "gpt-4-turbo", "label": "hijacked"})

        provider.refresh_from_db()
        self.assertEqual((provider.label, provider.model),
                         ("live", "gpt-4.1-mini"))


class AgentIdentityInEditorsTests(TestCase):
    """The name reaches six editor panels without six template changes."""

    def setUp(self):
        _platform()
        _register_test_surfaces()
        self.user = User.objects.create_user("reader", password="pw")
        self.client.force_login(self.user)

    def test_the_action_list_carries_the_agent(self):
        from .models import AiAgent

        AiAgent.objects.create(name="Ada", icon="fa-solid fa-robot",
                               tagline="Here to help.", active=True)

        payload = self.client.get(
            reverse("steven:surface_actions", args=["tests"])).json()

        self.assertEqual(payload["agent"]["name"], "Ada")
        self.assertEqual(payload["agent"]["icon"], "fa-solid fa-robot")

    def test_it_is_null_when_nobody_configured_one(self):
        """The panel falls back to calling itself Assistant."""
        payload = self.client.get(
            reverse("steven:surface_actions", args=["tests"])).json()

        self.assertIsNone(payload["agent"])

    def test_the_console_offers_setup_to_an_operator_only(self):
        response = self.client.get(reverse("steven:console"))
        self.assertNotContains(response, reverse("steven:manage"))

        self.client.force_login(User.objects.create_user(
            "boss2", password="pw", is_staff=True))

        self.assertContains(self.client.get(reverse("steven:console")),
                            reverse("steven:manage"))
