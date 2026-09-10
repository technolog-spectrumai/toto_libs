"""Which isolation a job actually gets, and whether the platform tells the truth.

The subsystem now has more than one tier, and the failure mode worth testing is
not "Kata is broken" — that fails loudly at the daemon. It is a host that
BELIEVES it runs jobs in virtual machines while running them in containers
beside the vault, because a name was misspelled or a flag went missing. Every
assertion here is about that.

Django-free by construction (no settings, no database), but it lives in the
Django test package so the gate runs it without a second harness.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from toto.anastasia.executor import drivers
from toto.anastasia.executor.drivers import docker as docker_driver
from toto.anastasia.families import PDF, PYTHON
from toto.anastasia.limits import Limits

RUN = Limits(cpu_millicores=1000, ram_mb=512, scratch_mb=256, pids=64)


def _argv(driver, *, family=PDF, network=None):
    return driver.build_run_args(
        family=family, limits=RUN, name="probe", cgroup_parent="g.slice",
        input_dir="/in", output_dir="/out", env={}, labels={}, argv=["x"],
        network=network)


class TierSelectionTests(SimpleTestCase):
    def test_an_unknown_tier_is_refused_rather_than_defaulted(self):
        """The whole reason `build_driver` exists.

        A typo resolving to "docker" would run other people's code on this
        kernel while the operator believed they had asked for VMs. That is the
        one outcome an isolation setting must never have, so it fails closed
        and the message names what does exist.
        """
        for name in ("kata-typo", "KATAA", "vm", ""):
            with self.subTest(tier=name):
                if name == "":
                    # Empty means "unset", which is the documented default.
                    self.assertEqual(drivers_build(name).name, "docker")
                    continue
                with self.assertRaises(drivers.DriverError) as caught:
                    drivers_build(name)
                self.assertIn("is not an isolation tier", str(caught.exception))

    def test_every_tier_reports_its_own_name(self):
        """`describe()` and the operator log read this. A driver that reported
        someone else's name would make the health page lie."""
        self.assertEqual(drivers_build("docker").name, "docker")
        self.assertEqual(drivers_build("kata").name, "kata")

    def test_the_tier_name_is_case_and_space_insensitive(self):
        for spelling in ("kata", "  kata ", "KATA"):
            with self.subTest(spelling=spelling):
                self.assertEqual(drivers_build(spelling).name, "kata")


class TierHardeningTests(SimpleTestCase):
    """The point of a subclass: one hardening table, not two."""

    def test_kata_adds_the_runtime_flag_AND_NOTHING_ELSE(self):
        """If these two argvs ever differ by more than `--runtime kata`, the
        security model has forked and one tier will drift behind the other."""
        plain = _argv(docker_driver.DockerClient())
        kata = _argv(docker_driver.KataDriver())
        self.assertEqual(len(kata), len(plain) + 2)
        self.assertEqual([a for a in kata if a not in plain],
                         ["--runtime", "kata"])
        self.assertEqual(kata[:3], ["create", "--runtime", "kata"])

    def test_the_docker_tier_names_no_runtime(self):
        """Absent, the daemon uses its default. Naming `runc` explicitly would
        be a lie on a host that configured something else as default."""
        self.assertNotIn("--runtime", _argv(docker_driver.DockerClient()))

    def test_every_tier_keeps_the_whole_hardening_table(self):
        """Named one at a time so a failure says WHICH guarantee was lost."""
        for tier in sorted(docker_driver.DRIVERS):
            argv = _argv(drivers_build(tier))
            for flag in ("--user", "--read-only", "--cap-drop",
                         "--security-opt", "--pids-limit", "--memory",
                         "--memory-swap", "--cpus", "--tmpfs", "--workdir"):
                with self.subTest(tier=tier, flag=flag):
                    self.assertIn(flag, argv)
            with self.subTest(tier=tier, flag="no-new-privileges"):
                self.assertIn("no-new-privileges", argv)
            with self.subTest(tier=tier, flag="--user is nobody"):
                self.assertEqual(argv[argv.index("--user") + 1],
                                 drivers.RUNNER_UID)

    def test_a_batch_family_gets_no_network_on_any_tier(self):
        for tier in sorted(docker_driver.DRIVERS):
            with self.subTest(tier=tier):
                argv = _argv(drivers_build(tier))
                self.assertEqual(argv[argv.index("--network") + 1], "none")

    def test_the_kernel_family_gets_one_network_on_any_tier(self):
        for tier in sorted(docker_driver.DRIVERS):
            with self.subTest(tier=tier):
                argv = _argv(drivers_build(tier), family=PYTHON,
                             network="zenobia_gears")
                self.assertEqual(argv[argv.index("--network") + 1],
                                 "zenobia_gears")

    def test_a_forbidden_variable_is_refused_on_any_tier(self):
        """The refusal lives in the shared half, so it cannot be a tier that
        forgets it."""
        for tier in sorted(docker_driver.DRIVERS):
            with self.subTest(tier=tier):
                with self.assertRaises(drivers.DriverError):
                    drivers_build(tier).build_run_args(
                        family=PDF, limits=RUN, name="p", cgroup_parent=None,
                        input_dir="/in", output_dir="/out",
                        env={"DB_PASSWORD": "hunter2"}, labels={},
                        argv=["x"], network=None)


class DriverErrorTests(SimpleTestCase):
    def test_docker_errors_are_driver_errors(self):
        """`service.py` catches the BASE and answers 502. If a tier's error
        stopped being one, its failures would become 500s instead."""
        self.assertTrue(issubclass(docker_driver.DockerError,
                                   drivers.DriverError))


def drivers_build(tier):
    return docker_driver.build_driver(tier)


def _rendered_source() -> str:
    """The Gear page's template with `{% comment %}` blocks stripped.

    The comments in that file EXPLAIN the interpolation trap, so they quote it
    — and a test that grepped the raw source would fail on the note warning
    against the very thing it checks for. Stripping them is what lets the
    prose stay where it is useful.
    """
    import re
    from pathlib import Path as _P

    import toto.anastasia as pkg

    page = (_P(pkg.__file__).parent / "templates" / "anastasia"
            / "index.html").read_text()
    return re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "",
                  page, flags=re.DOTALL)


class GuestOomVisibilityTests(SimpleTestCase):
    """A tier that cannot see a guest OOM must not report "not an OOM".

    Measured on a real machine 2026-09-10, same job and same ceiling, with
    /dev/shm deliberately larger than the memory limit so only RAM could bind:

        runc   exit 137, OOMKilled true
        kata   exit 255, OOMKilled false

    The ceiling is not weaker under Kata — it is stronger, since the VM is only
    as big as the limit. What is lost is the REPORT, and the report is what
    picks the sentence a user acts on.
    """

    def test_a_shared_kernel_driver_can_see_its_own_oom_kills(self):
        self.assertTrue(docker_driver.DockerClient.observes_guest_oom)

    def test_a_vm_driver_admits_it_cannot(self):
        """The whole point. If this ever flips to True, a Kata user whose job
        died for want of memory is told it was merely stopped."""
        self.assertFalse(docker_driver.KataDriver.observes_guest_oom)

    def test_the_default_is_the_safe_one_for_a_shared_kernel(self):
        """A driver that forgets to declare inherits True, which is right: the
        base contract is a host-visible cgroup kill, and a VM tier is the
        exception that has to say so."""
        self.assertTrue(drivers.Driver.observes_guest_oom)

    def test_exit_255_is_not_treated_as_proof_of_an_oom(self):
        """The tempting heuristic, refused on purpose.

        Kata reports 255 for a guest OOM — and also for a guest kernel panic
        and for a shim failure. Mapping 255 to "ran out of memory" would print
        a guess as a fact, which is the exact failure the tier-honesty rule
        exists to prevent. `exit_state` must report what it saw, nothing more.
        """
        client = docker_driver.KataDriver()
        client.inspect = lambda cid: {
            "State": {"ExitCode": 255, "OOMKilled": False, "Running": False}}
        state = client.exit_state("x")
        self.assertEqual(state["exit_code"], 255)
        self.assertFalse(
            state["oom_killed"],
            "the driver must not infer an OOM from an exit code")


class TierHonestyTests(SimpleTestCase):
    """The page must never promise isolation the runtime did not give.

    This is the assertion the whole tier feature exists for. Everything else
    here is about the argv; this is about the sentence a user reads and
    believes.
    """

    def test_the_kernel_isolating_set_is_a_whitelist(self):
        """Not `!= "docker"`. A tier this code has never heard of — a newer
        executor, a typo, a blank column on a Gear mounted before the field
        existed — must read as NOT proven rather than inheriting a promise
        from whatever it resembles."""
        from toto.anastasia.services import KERNEL_ISOLATING_TIERS

        self.assertEqual(KERNEL_ISOLATING_TIERS, frozenset({"kata"}))
        for unknown in ("", "docker", "fake", "runsc", "kata2", "KATA"):
            with self.subTest(tier=unknown):
                self.assertNotIn(unknown, KERNEL_ISOLATING_TIERS)

    def test_every_kernel_isolating_tier_is_a_real_driver(self):
        """A tier the page makes a promise about must be one the executor can
        actually run. Otherwise the strongest sentence is reachable by a
        configuration that refuses to boot."""
        from toto.anastasia.services import KERNEL_ISOLATING_TIERS

        for tier in KERNEL_ISOLATING_TIERS:
            with self.subTest(tier=tier):
                self.assertIn(tier, docker_driver.DRIVERS)
                self.assertEqual(drivers_build(tier).name, tier)

    def test_a_kernel_isolating_tier_passes_a_runtime_flag(self):
        """The claim and the argv must agree. A tier promising its own kernel
        while emitting no `--runtime` would be the exact lie: the daemon would
        hand the job to the default runtime and it would share this kernel."""
        from toto.anastasia.services import KERNEL_ISOLATING_TIERS

        for tier in KERNEL_ISOLATING_TIERS:
            with self.subTest(tier=tier):
                argv = _argv(drivers_build(tier))
                self.assertIn("--runtime", argv)
                self.assertEqual(argv[argv.index("--runtime") + 1], tier)

    def test_the_page_never_interpolates_the_tier_into_a_sentence(self):
        """Three hardcoded sentences, not one parameterised by the tier name.

        Asserted on the TEMPLATE SOURCE rather than on rendered output,
        because the failure is a `{{ gear.tier }}` somebody adds inside a
        trans block — which renders fine in every test that has a known tier
        and lies the day a new one appears.
        """
        from pathlib import Path as _P

        import toto.anastasia as pkg

        page = _rendered_source()
        self.assertIn("virtual machine", page,
                      "the strong sentence must exist to be guarded")
        for interpolation in ("{{ gear.tier }}", "{{ tier }}"):
            with self.subTest(interpolation=interpolation):
                self.assertNotIn(interpolation, page)

    def test_the_strong_sentence_is_guarded_by_the_boolean(self):
        """`isolates_kernel`, never `tier` truthiness. `{% if gear.tier %}`
        around the VM sentence would show it for every tier including
        docker."""
        from pathlib import Path as _P

        import toto.anastasia as pkg

        page = _rendered_source()
        strong = page.index("virtual machine")
        guard = page.rindex("{% if gear.isolates_kernel %}", 0, strong)
        # Nothing may reopen a branch between the guard and the claim.
        self.assertNotIn("{% if", page[guard + len("{% if gear.isolates_kernel %}"):strong])
