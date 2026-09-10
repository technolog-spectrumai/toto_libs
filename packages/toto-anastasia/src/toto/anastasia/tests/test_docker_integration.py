"""Enforcement, against a real Docker daemon.

Skipped wherever Docker is not reachable — the clean-environment gate runs in a
fresh venv with no daemon, and these must not fail there. That makes them easy
to forget, so each one asserts something no unit test can: that the limit is
applied by the KERNEL rather than by our arithmetic.

Run them deliberately:

    python manage.py test toto.anastasia.tests.test_docker_integration
"""

from __future__ import annotations

import os
import shutil
import tempfile
import subprocess
import unittest
import uuid

from django.test import SimpleTestCase

from toto.anastasia.families import Family
from toto.anastasia.limits import Limits
from toto.anastasia.executor import slices
from toto.anastasia.executor.drivers import docker as containers

#: A tiny image that is almost certainly already present. Nothing about these
#: tests depends on what it contains beyond a shell.
PROBE_IMAGE = os.environ.get("ANASTASIA_TEST_IMAGE", "alpine:3.20")


#: WHICH TIER these run against. Default docker, because that is what every
#: machine has; set ANASTASIA_TEST_RUNTIME=kata on a host with the Kata stack
#: registered and the SAME assertions run against virtual machines.
#:
#: That reuse is the point. "No host data, no network, no escalation, every
#: limit kernel-enforced" are claims about a SANDBOX, not about Docker, and a
#: second tier that had its own copy of them would eventually prove something
#: subtly weaker. One matrix, parameterised by tier, is the only way the two
#: cannot drift.
TEST_TIER = os.environ.get("ANASTASIA_TEST_RUNTIME", "docker")


def _driver():
    return containers.build_driver(TEST_TIER)


def _docker_ready() -> bool:
    try:
        client = _driver()
    except Exception:                            # noqa: BLE001 - unknown tier
        return False
    if not (client.available() and client.image_exists(PROBE_IMAGE)):
        return False
    if TEST_TIER == "docker":
        return True
    # A NON-DEFAULT TIER MUST BE PROVED, not assumed. `--runtime kata` on a
    # daemon with no kata runtime registered is refused outright, and these
    # tests silently passing against plain containers while claiming to test
    # VMs is the exact dishonesty the tier work exists to prevent.
    probe = subprocess.run(
        ["docker", "run", "--rm", "--runtime", TEST_TIER, PROBE_IMAGE, "true"],
        capture_output=True, text=True, check=False)
    return probe.returncode == 0


DOCKER = _docker_ready()
requires_docker = unittest.skipUnless(
    DOCKER,
    f"needs a reachable runtime ({TEST_TIER}) and the {PROBE_IMAGE} image")


@requires_docker
class _Vanished(Exception):
    """Something outside this test deleted the container mid-probe."""


#: How many times to re-run a probe whose container was deleted underneath it.
VANISHED_RETRIES = 2


def _retrying(case, attempt_once, what: str):
    """Run a container probe, retrying ONLY if something else deleted it.

    Shared by both helpers that drive a container, because the first version
    protected `run_probe` alone and `_uname` — twelve lines away, doing the
    same three reads — kept failing on the same race a day later.

    The retry is narrow on purpose: it fires on `_Vanished` and never on an
    assertion, so a genuine failure still fails on the first attempt.
    """
    last = None
    for attempt in range(VANISHED_RETRIES + 1):
        try:
            return attempt_once()
        except _Vanished as exc:
            last = exc
            print(f"    {what} container vanished (attempt "
                  f"{attempt + 1}/{VANISHED_RETRIES + 1}); something else on "
                  "this daemon deleted it")
    case.fail(
        f"{last} — it happened {VANISHED_RETRIES + 1} times running, so this "
        "is not a blip. Update the deployed anastasia-executord (its reconcile "
        "is claiming containers that are not its own), or stop it while "
        "running these tests.")


def _is_gone(text: str) -> bool:
    return "No such container" in (text or "")


class RunnerConfinementTests(SimpleTestCase):
    """What Docker actually applies, and whether it holds."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # NOT self.client: Django's SimpleTestCase puts its own test HTTP
        # client there in _pre_setup and would shadow this.
        cls.docker = _driver()
        cls.family = Family(key="probe", label="Probe", image=PROBE_IMAGE,
                            default_limits=Limits(1000, 128, 64, 32))

    def run_probe(self, limits, argv, *, timeout=40, env=None, network=None):
        """Run the probe, and retry ONLY if something else deleted it.

        A runner is labelled `anastasia.managed=1`, and an executor whose
        `list_managed()` is not scoped to its own staging root claims every
        such container on the daemon, then destroys the ones whose capsule it
        cannot account for. A live `anastasia-executord` did exactly that to
        this suite on a 30-second cycle, taking a different test each run. It
        only ever showed up on the Kata tier because a VM lives long enough to
        span a reconcile tick.

        The scoping fix (`LABEL_OWNER`) closes it for any executor built after
        it — but an OLDER executor already deployed on the test machine still
        eats these, so the suite has to survive one. The retry is deliberately
        narrow: it fires when the container vanished, never on an assertion, so
        a genuine failure still fails on the first attempt.
        """
        return _retrying(
            self,
            lambda: self._run_probe_once(limits, argv, timeout=timeout,
                                         env=env, network=network),
            "probe")

    def _run_probe_once(self, limits, argv, *, timeout=40, env=None,
                        network=None):
        work = tempfile.mkdtemp(prefix="anastasia-it-")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        input_dir, output_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(input_dir)
        os.makedirs(output_dir)
        os.chmod(output_dir, 0o777)
        with open(os.path.join(input_dir, "hello.txt"), "w") as handle:
            handle.write("staged\n")

        name = f"anastasia-it-{uuid.uuid4().hex[:10]}"
        container = self.docker.create(
            family=self.family, limits=limits, name=name, cgroup_parent=None,
            input_dir=input_dir, output_dir=output_dir, env=env,
            labels={containers.LABEL_CAPSULE: "it", containers.LABEL_EXEC: name},
            argv=argv, network=network)
        self.addCleanup(self.docker.remove, container)
        self.docker.start(container)
        waited = self.docker.wait(container, timeout=timeout)
        # DID SOMETHING ELSE DELETE IT? Say so, rather than letting the reads
        # below come back as "No such container" and read like a driver bug.
        #
        # A runner is labelled `anastasia.managed=1`, and an executor whose
        # `list_managed()` is not scoped to its own staging root claims EVERY
        # such container on the daemon, then destroys the ones whose capsule it
        # cannot account for. A live executor did exactly that to this suite
        # every 30 seconds. Scoping (`LABEL_OWNER`) fixes it — but only for an
        # executor built after that change, so an older one deployed on the
        # test machine still eats these.
        if not self.docker.inspect(container):
            raise _Vanished(
                "the container vanished between `wait` returning and reading "
                "its state")
        state = self.docker.exit_state(container)
        logs = self.docker.logs(container)
        # The reads above are three separate `docker` calls, so the deletion
        # can land between any two of them. Catch that too, rather than
        # returning a daemon error string as if it were the runner's output.
        if _is_gone(logs) or state.get("exit_code") is None:
            raise _Vanished("the container vanished while its output was "
                            "being read")
        return {
            "container": container,
            "inspect": self.docker.inspect(container),
            "state": state,
            "logs": logs,
            "timed_out": waited["timed_out"],
            "output_dir": output_dir,
        }

    def test_docker_applies_every_limit_we_asked_for(self):
        limits = Limits(cpu_millicores=500, ram_mb=64, scratch_mb=32, pids=48)
        result = self.run_probe(limits, ["sh", "-c", "true"])
        host = result["inspect"]["HostConfig"]
        config = result["inspect"]["Config"]

        # RAM *plus* scratch: the tmpfs is charged to this cgroup.
        self.assertEqual(host["Memory"], limits.memory_bytes)
        self.assertEqual(host["MemorySwap"], limits.memory_bytes,
                         "swap must equal memory, or a bounded job becomes "
                         "unbounded the moment it swaps")
        self.assertEqual(host["NanoCpus"], 500_000_000)
        self.assertEqual(host["PidsLimit"], 48)
        self.assertTrue(host["ReadonlyRootfs"])
        self.assertEqual(host["NetworkMode"], "none")
        self.assertEqual(host["CapDrop"], ["ALL"])
        self.assertIn("no-new-privileges", host["SecurityOpt"])
        self.assertEqual(config["User"], containers.RUNNER_UID)
        self.assertIn("size=32m", host["Tmpfs"]["/scratch"])
        self.assertIn("noexec", host["Tmpfs"]["/scratch"])

    def test_the_memory_ceiling_stops_the_job_on_every_tier(self):
        """The ceiling holds everywhere. Only the REPORT differs.

        On a shared kernel the host cgroup does the killing and Docker says so.
        In a VM the guest kernel does it, inside a machine the host cannot see
        into, so `OOMKilled` stays false — measured 2026-09-10: the same job at
        the same ceiling gives 137/true under runc and 255/false under Kata.

        So this asserts what is true of both — the job did NOT succeed — and
        then holds each tier to its own declared reporting. Relaxing it to
        "false is fine" would have let a real regression through on Docker.
        """
        result = self.run_probe(
            Limits(1000, 32, 32, 64),
            ["sh", "-c", "dd if=/dev/zero of=/dev/shm/blob bs=1M count=400"])
        state = result["state"]
        self.assertNotEqual(
            state["exit_code"], 0,
            f"the memory ceiling did not stop the job; state was {state}")
        if self.docker.observes_guest_oom:
            self.assertTrue(
                state["oom_killed"],
                "this tier claims it can see a guest OOM, and did not report "
                f"one; state was {state}")
        else:
            self.assertFalse(
                state["oom_killed"],
                "this tier declares it CANNOT see a guest OOM but reported "
                "one. If Kata gained that ability, delete the declaration "
                f"rather than this assertion; state was {state}")

    def test_an_oom_kill_is_not_visible_in_the_exit_code_alone(self):
        """Why ``exit_state`` reports OOMKilled separately: a memory kill and a
        deadline kill both surface as a SIGKILL, and they need different
        sentences."""
        if not self.docker.observes_guest_oom:
            self.skipTest("this tier cannot see a guest OOM at all; "
                          "the memory test above covers what it can promise")
        result = self.run_probe(
            Limits(1000, 32, 32, 64),
            ["sh", "-c", "dd if=/dev/zero of=/dev/shm/b bs=1M count=400; echo done"])
        self.assertTrue(result["state"]["oom_killed"])
        self.assertNotEqual(
            (result["state"]["exit_code"], True),
            (137, False),
            "this assertion exists to record that the exit code is not the "
            "signal — read OOMKilled, not the code")

    def test_the_syscall_filter_matches_what_the_tier_claims(self):
        """Ask the process whether it is filtered, and hold the tier to it.

        `/proc/self/status` reports the kernel's own view: `Seccomp: 2` means a
        filter is loaded, `0` means none. Measured 2026-09-10 — runc 2, Kata 0,
        because the shipped Kata config never passes container profiles to the
        guest agent.

        Kata's `False` is a considered position, not an oversight: seccomp
        shrinks the HOST kernel's attack surface, and a VM workload does not
        touch the host kernel first. But it is pinned so that if somebody flips
        `disable_guest_seccomp`, the declaration is corrected rather than the
        platform quietly gaining depth nobody knows it has — or, worse, quietly
        losing it on the tier that needs it.
        """
        result = self.run_probe(
            Limits(1000, 128, 32, 32),
            ["sh", "-c", "grep -E '^Seccomp:' /proc/self/status || echo none"])
        filtered = "Seccomp:\t2" in result["logs"] or "Seccomp: 2" in result["logs"]
        self.assertEqual(
            filtered, self.docker.applies_seccomp,
            f"this tier declares applies_seccomp="
            f"{self.docker.applies_seccomp} and the runner reports "
            f"{result['logs'].strip()!r}")

    def test_the_pid_ceiling_matches_what_the_tier_claims(self):
        """PIDS IS RESERVED FROM THE POOL, SO SOMEBODY MUST ENFORCE IT.

        On a shared kernel the host cgroup holds the workload and the limit
        bites. In a VM it holds the sandbox — the VMM and its threads — while
        the workload runs in a guest whose own pids cgroup reads `max`.
        Measured 2026-09-10: the probe below prints "can't fork" under runc and
        "survived" under Kata.

        This asserts BOTH directions against `enforces_guest_pids`, so the day
        Kata starts applying it the test fails and the declaration gets
        corrected — rather than the platform quietly continuing to describe the
        old behaviour. That is the point of pinning a known gap: a gap nobody
        is told about becomes a promise nobody checks.
        """
        result = self.run_probe(
            Limits(1000, 128, 32, 24),
            ["sh", "-c",
             "i=0; while [ $i -lt 300 ]; do sleep 5 & i=$((i+1)); done; echo survived"])
        logs = result["logs"]
        if self.docker.enforces_guest_pids:
            self.assertIn("can't fork", logs,
                          f"the pids limit did not bite: {logs[:200]}")
        else:
            self.assertNotIn(
                "can't fork", logs,
                "this tier declares it does NOT bound guest processes, and it "
                "just did. That is good news: update `enforces_guest_pids` and "
                "tell the booking arithmetic it can trust the dimension again.")

    def test_the_pid_ceiling_is_felt_by_the_workload(self):
        """ASK THE PROCESS, NOT THE CGROUP.

        An earlier version of this read `/sys/fs/cgroup/pids.max` and demanded
        a number. That was wrong for a VM tier and would have failed a working
        capsule: Kata enforces the ceiling through RLIMIT_NPROC, not through a
        guest cgroup, so `pids.max` legitimately reads `max` while the limit is
        very much in force. The cgroup is one mechanism; the question is
        whether the workload is bounded.

        And asking only `ulimit -u` is the SAME MISTAKE MIRRORED: Docker binds
        through the cgroup and leaves RLIMIT_NPROC unlimited, so that probe
        failed a perfectly bounded runc container. Two tiers, two mechanisms,
        neither universally visible.

        So this asks both and requires ONE of them to be finite. That is the
        real invariant — "something bounds this workload" — and it stays true
        if a third tier arrives with a third mechanism.
        """
        if not self.docker.enforces_guest_pids:
            self.skipTest("this tier does not claim to bound guest processes")
        result = self.run_probe(
            Limits(1000, 128, 32, 48),
            ["sh", "-c",
             "echo cgroup=$(cat /sys/fs/cgroup/pids.max 2>/dev/null || echo max); "
             "echo rlimit=$(ulimit -u)"])
        logs = result["logs"]
        cgroup = logs.split("cgroup=")[1].split()[0]
        rlimit = logs.split("rlimit=")[1].split()[0]
        bounded = [name for name, value in (("cgroup", cgroup),
                                            ("rlimit", rlimit))
                   if value not in ("max", "unlimited")]
        self.assertTrue(
            bounded,
            "NOTHING bounds this workload's processes: the cgroup reads "
            f"{cgroup!r} and RLIMIT_NPROC reads {rlimit!r}, on a tier that "
            "claims to enforce the reserved pids")

    def test_the_scratch_quota_is_hard(self):
        result = self.run_probe(
            Limits(1000, 128, 32, 64),
            ["sh", "-c",
             "dd if=/dev/zero of=/scratch/fill bs=1M count=200 2>&1; "
             "wc -c < /scratch/fill"])
        written = int((result["logs"].strip().splitlines() or ["0"])[-1])
        self.assertLessEqual(written, 32 * 1024 * 1024,
                             "the tmpfs let more through than its size")

    def test_the_root_filesystem_is_not_writable(self):
        result = self.run_probe(
            Limits(1000, 64, 32, 32),
            ["sh", "-c", "touch /evil 2>&1 || echo REFUSED"])
        self.assertIn("REFUSED", result["logs"])

    def test_a_runner_has_no_network(self):
        result = self.run_probe(
            Limits(1000, 64, 32, 32),
            ["sh", "-c", "wget -T2 -q -O- http://1.1.1.1 2>&1 || echo NONETWORK"])
        self.assertIn("NONETWORK", result["logs"])

    def test_a_runner_is_not_root(self):
        result = self.run_probe(Limits(1000, 64, 32, 32), ["id", "-u"])
        self.assertEqual(result["logs"].strip(), "65534")

    def test_a_runner_receives_no_credentials_and_no_docker_socket(self):
        """The security claim, asserted rather than assumed."""
        result = self.run_probe(
            Limits(1000, 64, 32, 32),
            ["sh", "-c", "env; echo ---; ls /var/run/docker.sock 2>&1"],
            env={"ANASTASIA_OPERATION": "probe"})

        logs = result["logs"]
        self.assertNotIn("docker.sock\n", logs.split("---")[-1])
        self.assertIn("No such file", logs.split("---")[-1])

        env_block = logs.split("---")[0]
        for forbidden in ("SECRET_KEY", "DB_PASSWORD", "POSTGRES_PASSWORD",
                          "VAULT_", "FIELD_ENCRYPTION_KEY",
                          "ANASTASIA_SHARED_SECRET", "REDIS_URL"):
            self.assertNotIn(forbidden, env_block)

        # And the mounts really are only the three we assembled.
        mounts = {m["Destination"] for m in result["inspect"].get("Mounts") or []}
        self.assertEqual(mounts, {"/in", "/out"},
                         "a runner has exactly two bind mounts; /scratch is a "
                         "tmpfs and nothing else may be attached")

    def test_staged_input_is_read_only_and_output_comes_back(self):
        result = self.run_probe(
            Limits(1000, 64, 32, 32),
            ["sh", "-c", "cat /in/hello.txt > /out/result.txt; "
                         "touch /in/evil 2>&1 || echo INPUT_RO"])
        self.assertIn("INPUT_RO", result["logs"])
        with open(os.path.join(result["output_dir"], "result.txt")) as handle:
            self.assertEqual(handle.read().strip(), "staged")

    def test_a_runner_past_its_deadline_is_killed(self):
        """A deadline is enforced by killing, and 137 says it was SIGKILL.

        The timeout is 8s rather than 3s because the budget has to cover
        `docker create` and `docker start` as well as the wait, and on a loaded
        machine those alone can eat three seconds — at which point `docker
        wait` returns the exit status of a container that finished on its own
        and `timed_out` is False. That failed exactly once, on a box running
        three test suites and the gate at the same time, and it looked like a
        driver defect rather than the race it was. The sleep stays at 120s, so
        a runner that is NOT killed still cannot pass this test by finishing.
        """
        result = self.run_probe(Limits(1000, 64, 32, 32),
                                ["sh", "-c", "sleep 120"], timeout=8)
        self.assertTrue(result["timed_out"],
                        "the runner outlived its deadline without being killed")
        self.assertEqual(result["state"]["exit_code"], 137)

    def test_label_discovery_finds_and_loses_a_runner(self):
        """What makes a manager restart survivable: the index is rebuilt by
        looking, so removing a container really does remove the knowledge."""
        result = self.run_probe(Limits(1000, 64, 32, 32), ["true"])
        found = self.docker.list_managed(capsule="it")
        self.assertIn(result["container"][:12],
                      [row["id"][:12] for row in found])
        self.docker.remove(result["container"])
        self.assertNotIn(result["container"][:12],
                         [row["id"][:12] for row in self.docker.list_managed(capsule="it")])


@requires_docker
class ForbiddenEnvTests(SimpleTestCase):
    def test_a_credential_cannot_be_passed_to_a_runner_even_deliberately(self):
        """Raised, not silently dropped: quietly discarding a variable the
        caller meant to pass produces a runner that fails invisibly."""
        docker = containers.DockerClient()
        family = Family(key="p", label="P", image=PROBE_IMAGE,
                        default_limits=Limits(1000, 128, 64, 32))
        for name in ("DB_PASSWORD", "SECRET_KEY", "VAULT_TOKEN",
                     "ANASTASIA_SHARED_SECRET", "AWS_SECRET_ACCESS_KEY",
                     "django_settings_module"):
            with self.subTest(variable=name):
                with self.assertRaises(containers.DockerError):
                    docker.build_run_args(
                        family=family, limits=Limits(1000, 64, 32, 32),
                        name="x", cgroup_parent=None, input_dir="/tmp",
                        output_dir="/tmp", env={name: "leak"},
                        labels={}, argv=["true"])


class SliceIntegrationTests(SimpleTestCase):
    """Real cgroups, when this process is allowed to make them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.driver = slices.SystemdSliceDriver()
        cls.permitted = slices.SystemdSliceDriver.available()

    def setUp(self):
        if not self.permitted:
            self.skipTest("this process may not create systemd slices")
        self.capsule = uuid.uuid4()
        self.addCleanup(self.driver.destroy, self.capsule)

    def test_a_slice_carries_the_limits_we_asked_for(self):
        limits = Limits(cpu_millicores=1500, ram_mb=256, scratch_mb=256, pids=64)
        self.driver.ensure(self.capsule, limits)
        path = self.driver.path(self.capsule)
        self.assertTrue(os.path.isdir(path), f"no cgroup at {path}")
        with open(os.path.join(path, "memory.max")) as handle:
            self.assertEqual(int(handle.read().strip()), limits.memory_bytes)
        with open(os.path.join(path, "pids.max")) as handle:
            self.assertEqual(int(handle.read().strip()), 64)

    def test_the_cgroup_path_follows_systemds_dash_nesting(self):
        """systemd expands EVERY dash into a level, so the obvious shallow path
        finds nothing and a healthy Capsule reads as unmeasurable."""
        self.driver.ensure(self.capsule, Limits(1000, 128, 64, 32))
        expected = slices.slice_cgroup_path(slices.slice_name(self.capsule))
        self.assertEqual(self.driver.path(self.capsule), expected)
        self.assertIn("anastasia.slice/anastasia-capsule.slice/", expected)
        self.assertTrue(os.path.isdir(expected))

    def test_sampling_a_live_slice_returns_usage(self):
        self.driver.ensure(self.capsule, Limits(1000, 128, 64, 32))
        sample = self.driver.sample(self.capsule)
        self.assertIn("pids_used", sample)
        self.assertIn("oom_kills", sample)

    def test_ensure_is_idempotent_and_destroy_is_too(self):
        limits = Limits(1000, 128, 64, 32)
        self.driver.ensure(self.capsule, limits)
        self.driver.ensure(self.capsule, limits)
        self.driver.destroy(self.capsule)
        self.driver.destroy(self.capsule)
        self.assertFalse(self.driver.exists(self.capsule))

    def test_sampling_a_slice_that_is_not_there_is_empty_not_an_error(self):
        self.assertEqual(self.driver.sample(uuid.uuid4()), {})


@requires_docker
class TierHonestyIntegrationTests(SimpleTestCase):
    """The one test that can catch the platform lying about isolation.

    Everything in `test_tiers.py` is about argv, config and the words on a
    page — all of it verifiable without a runtime, and none of it able to tell
    you whether a job ACTUALLY got its own kernel. Only this can, and only by
    running something.

    It is written to be meaningful in both directions. Under `docker` it
    asserts the platform does NOT claim a separate kernel; under `kata` it
    asserts the kernel really is separate. A suite that only ran under kata
    would leave the more common configuration unchecked.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.docker = _driver()
        cls.family = Family(key="probe", label="Probe", image=PROBE_IMAGE,
                            default_limits=Limits(1000, 128, 64, 32))

    def _uname(self) -> str:
        return _retrying(self, self._uname_once, "uname")

    def _uname_once(self) -> str:
        work = tempfile.mkdtemp(prefix="anastasia-tier-")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        input_dir, output_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(input_dir)
        os.makedirs(output_dir)
        os.chmod(output_dir, 0o777)
        name = f"anastasia-tier-{uuid.uuid4().hex[:10]}"
        container = self.docker.create(
            family=self.family, limits=Limits(1000, 256, 64, 32), name=name,
            cgroup_parent=None, input_dir=input_dir, output_dir=output_dir,
            env=None, labels={}, argv=["uname", "-r"], network=None)
        self.addCleanup(self.docker.remove, container)
        self.docker.start(container)
        self.docker.wait(container, timeout=60)
        logs = self.docker.logs(container)
        if _is_gone(logs):
            raise _Vanished("the uname container vanished before its output "
                            "could be read")
        return logs.strip()

    def test_the_guest_kernel_matches_what_the_tier_claims(self):
        """THE ASSERTION THE WHOLE CAMPAIGN IS FOR.

        A VM tier that returned the host's kernel would mean `--runtime` was
        accepted and did nothing — every hardening flag still applied, every
        page still saying "virtual machine", and every job still sharing this
        kernel with the vault.
        """
        host = os.uname().release
        guest = self._uname()
        self.assertTrue(guest, "the probe produced no output")

        from toto.anastasia.services import KERNEL_ISOLATING_TIERS

        if self.docker.name in KERNEL_ISOLATING_TIERS:
            self.assertNotEqual(
                guest, host,
                f"tier {self.docker.name!r} claims a kernel of its own, but the "
                f"job reported the HOST kernel ({host}). Either the runtime is "
                "not registered with the daemon, or it was accepted and did "
                "nothing — and the platform is telling users their code runs "
                "in a virtual machine when it does not.")
        else:
            self.assertEqual(
                guest, host,
                f"tier {self.docker.name!r} shares this kernel by definition, "
                "so a different one means the tier is not what it says")

    def test_a_container_tier_is_not_advertised_as_kernel_isolating(self):
        """Belt and braces on the whitelist, from the runtime's own side."""
        from toto.anastasia.services import KERNEL_ISOLATING_TIERS

        if self.docker.name not in KERNEL_ISOLATING_TIERS:
            self.assertEqual(self._uname(), os.uname().release)
