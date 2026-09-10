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

    def test_no_family_can_be_kept_warm(self):
        """Warm pools are gone, and the FIELD is gone with them.

        This was `test_only_a_long_lived_runtime_is_warmable`, asserting that
        batch families are never kept alive between executions. Warm pools were
        deleted on 2026-09-10 — a fresh sandbox per job makes them meaningless,
        and they were already vestigial (`served_warm` was hardcoded False and
        nothing ever wrote a WARM event).

        The assertion INVERTS rather than being deleted: a `warmable` attribute
        growing back on the dataclass would mean somebody restored the storage
        for a feature nothing operates, which is exactly how it survived unused
        for a month the first time.
        """
        for key, fam in families.FAMILIES.items():
            with self.subTest(family=key):
                self.assertFalse(hasattr(fam, "warmable"),
                                 f"{key} carries a warmable flag again")

    def test_a_wheelhouse_selection_takes_names_and_nothing_else(self):
        """The ``dists`` kind: package NAMES, never a requirement specifier.

        A wheelhouse pins exactly one build of each distribution, so a version
        in the request could only agree with the pin or contradict it. Refusing
        the whole grammar is simpler than resolving that, and it keeps the
        parameter far away from argv injection: the names reach pip as separate
        arguments after ``--no-index``, and there is nothing else to spell.
        """
        param = families.Param(name="packages", kind="dists")
        for good in ("numpy", "numpy+pandas", "scikit-learn"):
            with self.subTest(value=good):
                self.assertEqual(param.clean(good), good)

    def test_a_wheelhouse_selection_is_normalised_the_way_pep_503_says(self):
        """PEP 503 compares names case-insensitively, so a person typing
        "NumPy" means numpy. Normalising HERE, on the trusted side, is what
        lets the check against the manifest be a plain string equality."""
        param = families.Param(name="packages", kind="dists")
        self.assertEqual(param.clean("NumPy"), "numpy")
        self.assertEqual(param.clean("SciPy+Pandas"), "scipy+pandas")
        # A run of separators collapses, so these name ONE distribution each.
        self.assertEqual(param.clean("zope.interface"), "zope-interface")
        self.assertEqual(param.clean("num_py"), "num-py")
        self.assertEqual(param.clean("numpy--x"), "numpy-x")

    def test_a_wheelhouse_selection_refuses_everything_else(self):
        """Each of these was tried against the real validator.

        The interesting ones are not the shell metacharacters — they are
        ``numpy==1.2`` (a version, i.e. the requirement grammar this kind
        exists to exclude) and ``numpy+numpy``, which would otherwise install
        one distribution twice and read as a manifest disagreement later.
        """
        param = families.Param(name="packages", kind="dists")
        for bad in ("numpy==1.2", "numpy>=1.0", "numpy pandas",
                    "../etc/passwd", "numpy;rm -rf /", "numpy&&curl",
                    "$(id)", "-numpy", "numpy+", "+numpy", "",
                    "numpy+numpy", "numpy+NumPy", "zope.interface+zope_interface",
                    "http://example.invalid/x.whl"):
            with self.subTest(value=bad):
                with self.assertRaises(families.ParamError):
                    param.clean(bad)

    def test_no_family_can_ask_for_a_network(self):
        """ZERO postures. Every runner of every family is `--network none`.

        There were two fields and they are both gone: `needs_egress` (a package
        install reaching an index) and `kernel_link` (the python family joining
        the Capsule's internal network so the web tier could reach a long-lived
        kernel's ZMQ ports). Neither job exists any more.

        Asserted as the ABSENCE OF THE FIELDS rather than as "no family sets
        them to True", because that is the failure this guards. A field left on
        the dataclass and set False everywhere is one somebody flips back on
        for a plausible-sounding reason, and `hasattr` goes red the moment one
        reappears — where a truthiness check would quietly pass.
        """
        for key, fam in families.FAMILIES.items():
            with self.subTest(family=key):
                for gone in ("kernel_link", "needs_internal_network"):
                    self.assertFalse(
                        hasattr(fam, gone),
                        f"{key} declares {gone} again — a runner that can be "
                        f"reached, or reach out, is a new posture and needs a "
                        f"decision, not a default")

    def test_nothing_can_reach_the_internet(self):
        """The one posture that reached off this machine is GONE.

        `needs_egress` existed for a single job — fetching declared packages
        from an index — and both families that declared it (python-install,
        python-connected) were deleted on 2026-09-10 along with the operations
        that named them. Dependencies are baked into the runner images.

        Asserted three ways, because each catches a different way back in: no
        family declares such a field, the catalogue holds only the five known
        families, and no operation names a family outside it. If a batch family
        ever gains egress, a job that renders somebody else's document has an
        exfiltration path.
        """
        for key, fam in families.FAMILIES.items():
            with self.subTest(family=key):
                self.assertFalse(hasattr(fam, "needs_egress"),
                                 f"{key} declares an egress posture again")
        self.assertEqual(set(families.FAMILIES),
                         {"pdf", "latex", "media", "ocr", "python"})
        for name, op in families.OPERATIONS.items():
            with self.subTest(operation=name):
                self.assertIn(op.family.key, families.FAMILIES)

    def test_the_install_operation_is_gone(self):
        """Nothing may ask a Capsule to install packages.

        `install_python_packages` and `start_python_runtime_connected` were the
        two operations with egress. Both are deleted; asking for either must be
        a refusal naming what exists, not a KeyError.
        """
        for name in ("install_python_packages",
                     "start_python_runtime_connected"):
            with self.subTest(operation=name):
                self.assertNotIn(name, families.OPERATIONS)
                with self.assertRaises(families.ParamError):
                    families.operation(name)

    def test_every_family_default_is_above_the_reservation_floor(self):
        """A family whose default runner could not fit in the smallest legal
        Capsule would be undiscoverable until someone tried it."""
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
