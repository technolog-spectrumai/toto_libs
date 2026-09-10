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

from types import SimpleNamespace

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

    def test_kata_adds_the_runtime_flag_AND_ONE_DOCUMENTED_COMPENSATION(self):
        """The tiers may differ ONLY where a mechanism genuinely differs.

        This began as "and nothing else", which was right while `--runtime`
        was the only difference. It now permits exactly one more: a VM's guest
        never receives `--pids-limit` (the kata shim strips
        `linux.resources.pids` from the spec), so `--ulimit nproc` re-imposes
        the SAME promise by the only route that survives. See
        `KataDriver.build_run_args`.

        The list is deliberately a closed literal rather than a "kata may add
        extra flags" allowance. A second entry appearing here should require
        somebody to write down why, in this test, next to this sentence —
        which is the whole point of pinning it.
        """
        plain = _argv(docker_driver.DockerClient())
        kata = _argv(docker_driver.KataDriver())
        extra = [a for a in kata if a not in plain]
        self.assertEqual(
            extra,
            ["--runtime", "kata", "--ulimit", f"nproc={RUN.pids}:{RUN.pids}"],
            "the tiers have diverged by something undocumented")
        self.assertEqual(kata[:3], ["create", "--runtime", "kata"])

    def test_the_pids_compensation_carries_the_reserved_number(self):
        """Not a constant. Whatever the user reserved is what is enforced."""
        limits = Limits(cpu_millicores=500, ram_mb=64, scratch_mb=32, pids=17)
        argv = docker_driver.KataDriver().build_run_args(
            family=PDF, limits=limits, name="p", cgroup_parent=None,
            input_dir="/in", output_dir="/out", env={}, labels={},
            argv=["x"], network=None)
        self.assertIn("nproc=17:17", argv)

    def test_the_docker_tier_does_not_carry_the_compensation(self):
        """A shared kernel already enforces `--pids-limit`. Adding a second,
        weaker, per-UID mechanism there would be noise implying a doubt that
        does not exist."""
        self.assertNotIn("--ulimit", _argv(docker_driver.DockerClient()))

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
                             network="zenobia_capsules")
                self.assertEqual(argv[argv.index("--network") + 1],
                                 "zenobia_capsules")

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
    """The Capsule page's template with `{% comment %}` blocks stripped.

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


class RunnerOwnershipTests(SimpleTestCase):
    """One daemon, more than one executor. They must not eat each other.

    `anastasia.managed=1` says "an anastasia runner". It does not say WHOSE.
    `list_managed()` filtered on that alone, so it returned every anastasia
    container on the daemon, and `destroy_orphan_runners` then removed the ones
    it could not account for — correctly, by its own lights, because their capsules
    have no staging directory under ITS root.

    Two consequences, one hypothetical and one that actually happened:

    * two deployments sharing a Docker daemon would destroy each other's
      RUNNING JOBS, continuously, each believing it was tidying up;
    * the live executor on this machine deleted the integration suite's
      containers every 30 seconds, which is the whole "flaky under Kata" story
      — Kata is slow enough that a container lives across a reconcile tick.

    The owner is the executor's staging root, because that is the one thing an
    executor uniquely owns and already knows.
    """

    def test_a_runner_is_stamped_with_its_owner(self):
        driver = docker_driver.DockerClient(owner="/var/lib/anastasia/staging")
        argv = _argv(driver)
        self.assertIn("anastasia.owner=/var/lib/anastasia/staging", argv)

    def test_listing_asks_only_for_its_own(self):
        driver = docker_driver.DockerClient(owner="/srv/one")
        seen = {}

        def spy(args, **kwargs):
            seen["argv"] = args
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        driver._run = spy
        driver.list_managed()
        self.assertIn("label=anastasia.owner=/srv/one", seen["argv"])

    def test_two_executors_do_not_see_each_other(self):
        """The regression, stated as the thing that went wrong."""
        mine = docker_driver.DockerClient(owner="/srv/mine")
        theirs = docker_driver.DockerClient(owner="/srv/theirs")
        self.assertNotEqual(
            [a for a in _argv(mine) if a.startswith("anastasia.owner=")],
            [a for a in _argv(theirs) if a.startswith("anastasia.owner=")])

    def test_an_unowned_runner_belongs_to_nobody(self):
        """THE SAFE DIRECTION, and it is deliberate.

        A driver with no owner stamps no owner label and filters on none. So a
        container from before this existed is claimed by no executor and is
        therefore destroyed by none: it leaks rather than being deleted while
        somebody's job runs in it. Leaking a container is recoverable by hand.
        Destroying another deployment's running job is not.
        """
        argv = _argv(docker_driver.DockerClient())
        self.assertFalse([a for a in argv if a.startswith("anastasia.owner=")])

    def test_the_manager_tells_its_driver_who_it_is(self):
        """The manager knows the staging root; `build_driver()` does not. If
        this wiring is lost the labels stop being stamped and the fleet-wide
        deletion comes back silently."""
        from toto.anastasia.executor import capsules

        driver = docker_driver.DockerClient()
        capsules.CapsuleManager(staging_root="/srv/here", docker=driver,
                          slice_driver=_NullSlices())
        self.assertEqual(driver.owner, "/srv/here")


class _NullSlices:
    """Enough of a slice driver for a manager to construct."""

    name = "null"
    enforced = False

    def ensure(self, *a, **k):
        pass

    def destroy(self, *a, **k):
        pass

    def cgroup_parent(self, *a, **k):
        return None


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
        executor, a typo, a blank column on a Capsule mounted before the field
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
        because the failure is a `{{ capsule.tier }}` somebody adds inside a
        trans block — which renders fine in every test that has a known tier
        and lies the day a new one appears.
        """
        from pathlib import Path as _P

        import toto.anastasia as pkg

        page = _rendered_source()
        self.assertIn("virtual machine", page,
                      "the strong sentence must exist to be guarded")
        for interpolation in ("{{ capsule.tier }}", "{{ tier }}"):
            with self.subTest(interpolation=interpolation):
                self.assertNotIn(interpolation, page)

    def test_the_strong_sentence_is_guarded_by_the_boolean(self):
        """`isolates_kernel`, never `tier` truthiness. `{% if capsule.tier %}`
        around the VM sentence would show it for every tier including
        docker."""
        from pathlib import Path as _P

        import toto.anastasia as pkg

        page = _rendered_source()
        strong = page.index("virtual machine")
        guard = page.rindex("{% if capsule.isolates_kernel %}", 0, strong)
        # Nothing may reopen a branch between the guard and the claim.
        self.assertNotIn("{% if", page[guard + len("{% if capsule.isolates_kernel %}"):strong])


class ArgvInjectionTests(SimpleTestCase):
    """Nothing a caller supplies may become a docker FLAG.

    Every value below reaches `build_run_args` from somewhere a user can
    influence. The protection is that they are placed as separate argv
    elements after `--`-style positional boundaries rather than interpolated
    into a string — but that is a property worth asserting rather than
    trusting, because one f-string would undo it silently.
    """

    def test_a_name_that_looks_like_a_flag_stays_one_element(self):
        argv = docker_driver.DockerClient().build_run_args(
            family=PDF, limits=RUN, name="--privileged", cgroup_parent=None,
            input_dir="/in", output_dir="/out", env={}, labels={},
            argv=["x"], network=None)
        # It appears exactly once, as the VALUE after --name, and never as a
        # standalone element docker would read as a flag.
        self.assertEqual(argv[argv.index("--name") + 1], "--privileged")
        self.assertEqual(argv.count("--privileged"), 1)

    def test_the_command_is_never_merged_into_one_string(self):
        """`sh -c "sleep 120"` must stay three elements. Joining them is how a
        shell metacharacter in a filename becomes a command."""
        argv = docker_driver.DockerClient().build_run_args(
            family=PDF, limits=RUN, name="p", cgroup_parent=None,
            input_dir="/in", output_dir="/out", env={}, labels={},
            argv=["sh", "-c", "echo hi; rm -rf /"], network=None)
        self.assertIn("echo hi; rm -rf /", argv)
        self.assertNotIn("sh -c echo hi; rm -rf /", " ".join(argv[:-3]))

    def test_a_label_value_cannot_add_a_flag(self):
        argv = docker_driver.DockerClient().build_run_args(
            family=PDF, limits=RUN, name="p", cgroup_parent=None,
            input_dir="/in", output_dir="/out", env={},
            labels={"anastasia.capsule": "x --privileged"},
            argv=["true"], network=None)
        self.assertNotIn("--privileged", argv)

    def test_every_limit_reaches_the_argv_as_a_number(self):
        """A limit that arrived as a string would be accepted by docker and
        then mean something else, or nothing."""
        argv = docker_driver.DockerClient().build_run_args(
            family=PDF, limits=RUN, name="p", cgroup_parent=None,
            input_dir="/in", output_dir="/out", env={}, labels={},
            argv=["true"], network=None)
        self.assertEqual(argv[argv.index("--memory") + 1],
                         str(RUN.memory_bytes))
        self.assertEqual(argv[argv.index("--pids-limit") + 1], str(RUN.pids))


class ForbiddenEnvTests(SimpleTestCase):
    """A credential must not reach a runner even when a caller insists.

    The screen RAISES rather than dropping the variable. Silently discarding
    it would let a caller believe a secret was delivered, and the job would
    fail somewhere far from the cause.
    """

    def _build(self, env):
        return docker_driver.DockerClient().build_run_args(
            family=PDF, limits=RUN, name="p", cgroup_parent=None,
            input_dir="/in", output_dir="/out", env=env, labels={},
            argv=["true"], network=None)

    def test_the_obvious_credentials_are_refused(self):
        for name in ("SECRET_KEY", "DB_PASSWORD", "POSTGRES_PASSWORD",
                     "VAULT_KEY", "FIELD_ENCRYPTION_KEY",
                     "ANASTASIA_SHARED_SECRET"):
            with self.subTest(variable=name):
                with self.assertRaises(drivers.DriverError):
                    self._build({name: "x"})

    def test_the_screen_is_by_PREFIX_so_a_suffix_cannot_dodge_it(self):
        with self.assertRaises(drivers.DriverError):
            self._build({"DB_PASSWORD_BACKUP": "x"})

    def test_an_allowed_variable_still_gets_through(self):
        argv = self._build({"ANASTASIA_OPERATION": "render_pdf"})
        self.assertIn("ANASTASIA_OPERATION=render_pdf", argv)

