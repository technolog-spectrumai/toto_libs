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
from .surfaces import Action, AiSurface, registry
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
