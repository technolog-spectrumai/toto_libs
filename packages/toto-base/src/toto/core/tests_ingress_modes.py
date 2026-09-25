"""The three ingress modes, and the boolean they replaced."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings

from toto.features import (INGRESS_FULL, INGRESS_NONE, INGRESS_REALISTIC,
                           IngressModeError, ingress_mode)
from toto.ingress import IngressCommand


class ResolveTests(SimpleTestCase):
    def _mode(self, **env):
        return ingress_mode(env.get)

    def test_absent_means_realistic(self):
        self.assertEqual(self._mode(), INGRESS_REALISTIC)

    def test_the_old_boolean_still_decides_when_alone(self):
        self.assertEqual(self._mode(FULL_INGRESS="1"), INGRESS_FULL)
        self.assertEqual(self._mode(FULL_INGRESS="0"), INGRESS_REALISTIC)
        self.assertEqual(self._mode(FULL_INGRESS=True), INGRESS_FULL)

    def test_words_and_digits(self):
        self.assertEqual(self._mode(INGRESS_MODE="none"), INGRESS_NONE)
        self.assertEqual(self._mode(INGRESS_MODE="0"), INGRESS_NONE)
        self.assertEqual(self._mode(INGRESS_MODE="Realistic"), INGRESS_REALISTIC)
        self.assertEqual(self._mode(INGRESS_MODE="1"), INGRESS_REALISTIC)
        self.assertEqual(self._mode(INGRESS_MODE="full"), INGRESS_FULL)
        self.assertEqual(self._mode(INGRESS_MODE="2"), INGRESS_FULL)

    def test_an_unknown_word_is_refused(self):
        with self.assertRaises(IngressModeError):
            self._mode(INGRESS_MODE="demo")

    def test_a_disagreement_is_refused(self):
        with self.assertRaises(IngressModeError):
            self._mode(INGRESS_MODE="realistic", FULL_INGRESS="1")
        with self.assertRaises(IngressModeError):
            self._mode(INGRESS_MODE="full", FULL_INGRESS="0")

    def test_agreement_and_none_beside_zero_pass(self):
        self.assertEqual(self._mode(INGRESS_MODE="full", FULL_INGRESS="1"), INGRESS_FULL)
        self.assertEqual(self._mode(INGRESS_MODE="none", FULL_INGRESS="0"), INGRESS_NONE)
        self.assertEqual(self._mode(INGRESS_MODE="realistic", FULL_INGRESS=""), INGRESS_REALISTIC)


class _Probe(IngressCommand):
    """Records what the base class decided; seeds nothing."""
    bootstrap_economy = False
    seen = None

    def bootstrap(self):
        _Probe.seen = ("bootstrap", self.mode, self.full)
        super().bootstrap()

    def process(self):
        _Probe.seen = ("process", self.mode, self.full)


class CommandTests(TestCase):
    def _run(self, **opts):
        _Probe.seen = None
        cmd = _Probe()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()
        parser = cmd.create_parser("manage.py", "probe")
        options = parser.parse_args([]).__dict__
        options.update(opts)
        cmd.handle(**options)
        return _Probe.seen

    def test_full_is_the_full_mode(self):
        self.assertEqual(self._run(full=True), ("process", INGRESS_FULL, True))

    def test_mode_realistic_is_not_full(self):
        self.assertEqual(self._run(mode="realistic"), ("process", INGRESS_REALISTIC, False))

    def test_the_two_spellings_may_not_disagree(self):
        with self.assertRaises(CommandError):
            self._run(mode="realistic", full=True)

    def test_none_returns_before_bootstrap_and_process(self):
        self.assertIsNone(self._run(mode="none"))

    @override_settings(INGRESS_MODE="full")
    def test_the_host_setting_is_the_default(self):
        self.assertEqual(self._run(), ("process", INGRESS_FULL, True))

    @override_settings(INGRESS_MODE="none", INGRESS_ALLOWED_APPS=["toto.core"])
    def test_ingress_all_seeds_nothing_in_mode_none(self):
        out = StringIO()
        call_command("ingress_all", stdout=out)
        self.assertIn("mode none", out.getvalue())
        self.assertNotIn("Summary", out.getvalue())

    @override_settings(INGRESS_MODE="realistic",
                       INGRESS_ALLOWED_APPS=["toto.does_not_exist"])
    def test_strict_fails_the_run_on_a_failed_command(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("ingress_all", strict=True, stdout=out)
        call_command("ingress_all", stdout=out)   # not strict: reported only
