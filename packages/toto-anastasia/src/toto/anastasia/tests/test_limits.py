"""The four-dimensional arithmetic, and what it refuses at the API boundary."""

from __future__ import annotations

from django.test import SimpleTestCase

from toto.anastasia import families
from toto.anastasia.limits import Limits, LimitsError, validate_reservation


class ArithmeticTests(SimpleTestCase):
    def test_subtraction_clamps_at_zero_in_every_dimension(self):
        small, big = Limits(100, 100, 100, 100), Limits(500, 500, 500, 500)
        self.assertEqual((small - big).as_dict(), Limits().as_dict())

    def test_fits_in_requires_every_dimension(self):
        budget = Limits(1000, 1000, 1000, 1000)
        self.assertTrue(Limits(1000, 1000, 1000, 1000).fits_in(budget))
        # One dimension over is not a fit, however comfortable the other three.
        self.assertFalse(Limits(1, 1, 1, 1001).fits_in(budget))

    def test_memory_ceiling_includes_scratch(self):
        """tmpfs pages are charged to the cgroup that faults them in, so a
        ceiling of RAM alone would let a full scratch OOM the compute."""
        limits = Limits(cpu_millicores=1000, ram_mb=512, scratch_mb=256, pids=64)
        self.assertEqual(limits.memory_bytes, (512 + 256) * 1024 * 1024)

    def test_cpu_quota_rounds_up_so_it_is_never_zero(self):
        """systemd reads CPUQuota=0 as "no CPU at all", which hangs a runner
        instead of throttling it."""
        self.assertEqual(Limits(cpu_millicores=1).cpu_quota_percent, 1)
        self.assertEqual(Limits(cpu_millicores=100).cpu_quota_percent, 10)
        self.assertEqual(Limits(cpu_millicores=2500).cpu_quota_percent, 250)

    def test_docker_cpus_is_exact_for_millicores(self):
        self.assertEqual(Limits(cpu_millicores=1500).docker_cpus, "1.500")


class BoundaryTests(SimpleTestCase):
    def test_a_float_is_refused_rather_than_rounded(self):
        with self.assertRaises(LimitsError):
            Limits.from_mapping({"ram_mb": 1.5})

    def test_a_bool_is_refused_even_though_it_is_an_int(self):
        with self.assertRaises(LimitsError):
            Limits.from_mapping({"pids": True})

    def test_a_negative_is_refused(self):
        with self.assertRaises(LimitsError):
            Limits.from_mapping({"cpu_millicores": -1})

    def test_an_unknown_dimension_is_refused_by_name(self):
        with self.assertRaises(LimitsError) as caught:
            Limits.from_mapping({"gpus": 4})
        self.assertIn("gpus", str(caught.exception))

    def test_a_reservation_nothing_could_run_in_is_refused(self):
        with self.assertRaises(LimitsError):
            validate_reservation(Limits(10, 10, 10, 10))


class CatalogueTests(SimpleTestCase):
    def test_every_operation_names_a_real_family(self):
        for op in families.OPERATIONS.values():
            self.assertIn(op.family.key, families.FAMILIES)

    def test_every_family_image_is_prefixed_so_reconciliation_can_find_them(self):
        for fam in families.FAMILIES.values():
            self.assertTrue(fam.image.startswith("anastasia-"), fam.image)

    def test_only_the_python_family_is_warmable(self):
        """Warmth must be earned. A batch family is cheap to recreate, so
        keeping one alive only holds the user's own Gear capacity idle."""
        warm = {k for k, f in families.FAMILIES.items() if f.warmable}
        self.assertEqual(warm, {"python"})

    def test_only_the_python_family_gets_a_network(self):
        networked = {k for k, f in families.FAMILIES.items()
                     if f.needs_internal_network}
        self.assertEqual(networked, {"python"})

    def test_every_family_default_is_above_the_reservation_floor(self):
        """A family whose default runner could not fit in the smallest legal
        Gear would be undiscoverable until someone tried it."""
        for fam in families.FAMILIES.values():
            with self.subTest(family=fam.key):
                validate_reservation(fam.default_limits)

    def test_the_longest_operation_stays_under_the_sweeper_cutoff(self):
        """The sweep cutoff is DERIVED from this. If an operation ever gets a
        longer max_timeout, sweeps.py must move with it — this is the test
        that says so."""
        from toto.anastasia import sweeps  # noqa: F401  (registers on import)
        longest = max(op.max_timeout for op in families.OPERATIONS.values())
        self.assertLess(longest, 10800)

    def test_every_media_command_is_a_closed_choice(self):
        """The vocabulary has no escape hatch.

        The fileservices ffmpeg plugin this replaces took a free-text argument
        string and rejected shell metacharacters — a defence that has to be
        right every time. A closed enum needs no defence.
        """
        op = families.operation("run_media_command")
        command = next(p for p in op.params if p.name == "command")
        self.assertEqual(command.kind, "enum")
        self.assertEqual(set(command.choices), set(families.MEDIA_COMMANDS))
        with self.assertRaises(families.ParamError):
            op.clean({"command": "rm -rf /", "input": "a.mp4"})

    def test_a_media_output_name_cannot_become_a_path_or_an_extension(self):
        op = families.operation("run_media_command")
        for hostile in ("../evil", "a/b", "with space", "clip.mp4", ""):
            with self.subTest(name=hostile):
                with self.assertRaises(families.ParamError):
                    op.clean({"command": "resize", "input": "a.mp4",
                              "output_name": hostile})

    def test_media_geometry_is_bounded(self):
        op = families.operation("run_media_command")
        for field, value in (("width", 99999), ("height", -3), ("fps", 0),
                             ("x", -1)):
            with self.subTest(field=field):
                with self.assertRaises(families.ParamError):
                    op.clean({"command": "resize", "input": "a.mp4",
                              field: value})

    def test_a_time_parameter_cannot_carry_a_command(self):
        op = families.operation("run_media_command")
        for hostile in ("; rm -rf /", "$(id)", "00:00:00 && ls", "yesterday"):
            with self.subTest(value=hostile):
                with self.assertRaises(families.ParamError):
                    op.clean({"command": "cut", "input": "a.mp4",
                              "start_time": hostile})
        # …and the real forms still pass.
        for good in ("90", "00:01:30", "00:01:30.500", "5"):
            with self.subTest(value=good):
                op.clean({"command": "cut", "input": "a.mp4",
                          "start_time": good})

    def test_defaults_survive_a_clean_round_trip(self):
        op = families.operation("run_ocr")
        self.assertEqual(op.clean({"input": "a.png"}),
                         {"input": "a.png", "lang": "eng", "psm": 3})

    def test_a_required_parameter_is_required(self):
        with self.assertRaises(families.ParamError):
            families.operation("compile_latex").clean({})
