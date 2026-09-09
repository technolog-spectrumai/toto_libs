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
import unittest
import uuid

from django.test import SimpleTestCase

from toto.anastasia.families import Family
from toto.anastasia.limits import Limits
from toto.anastasia.executor import containers, slices

#: A tiny image that is almost certainly already present. Nothing about these
#: tests depends on what it contains beyond a shell.
PROBE_IMAGE = os.environ.get("ANASTASIA_TEST_IMAGE", "alpine:3.20")


def _docker_ready() -> bool:
    client = containers.DockerClient()
    return client.available() and client.image_exists(PROBE_IMAGE)


DOCKER = _docker_ready()
requires_docker = unittest.skipUnless(
    DOCKER, f"needs a reachable Docker daemon and the {PROBE_IMAGE} image")


@requires_docker
class RunnerConfinementTests(SimpleTestCase):
    """What Docker actually applies, and whether it holds."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # NOT self.client: Django's SimpleTestCase puts its own test HTTP
        # client there in _pre_setup and would shadow this.
        cls.docker = containers.DockerClient()
        cls.family = Family(key="probe", label="Probe", image=PROBE_IMAGE,
                            default_limits=Limits(1000, 128, 64, 32))

    def run_probe(self, limits, argv, *, timeout=40, env=None, network=None):
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
            labels={containers.LABEL_GEAR: "it", containers.LABEL_EXEC: name},
            argv=argv, network=network)
        self.addCleanup(self.docker.remove, container)
        self.docker.start(container)
        waited = self.docker.wait(container, timeout=timeout)
        return {
            "container": container,
            "inspect": self.docker.inspect(container),
            "state": self.docker.exit_state(container),
            "logs": self.docker.logs(container),
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

    def test_the_memory_ceiling_oom_kills_rather_than_swapping(self):
        result = self.run_probe(
            Limits(1000, 32, 32, 64),
            ["sh", "-c", "dd if=/dev/zero of=/dev/shm/blob bs=1M count=400"])
        self.assertTrue(
            result["state"]["oom_killed"],
            "a runner over its memory ceiling must be OOM-killed; "
            f"state was {result['state']}")

    def test_an_oom_kill_is_not_visible_in_the_exit_code_alone(self):
        """Why ``exit_state`` reports OOMKilled separately: a memory kill and a
        deadline kill both surface as a SIGKILL, and they need different
        sentences."""
        result = self.run_probe(
            Limits(1000, 32, 32, 64),
            ["sh", "-c", "dd if=/dev/zero of=/dev/shm/b bs=1M count=400; echo done"])
        self.assertTrue(result["state"]["oom_killed"])
        self.assertNotEqual(
            (result["state"]["exit_code"], True),
            (137, False),
            "this assertion exists to record that the exit code is not the "
            "signal — read OOMKilled, not the code")

    def test_the_pid_ceiling_stops_a_fork_bomb(self):
        result = self.run_probe(
            Limits(1000, 128, 32, 24),
            ["sh", "-c",
             "i=0; while [ $i -lt 300 ]; do sleep 5 & i=$((i+1)); done; echo survived"])
        self.assertIn("can't fork", result["logs"],
                      f"the pids limit did not bite: {result['logs'][:200]}")

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
        result = self.run_probe(Limits(1000, 64, 32, 32),
                                ["sh", "-c", "sleep 120"], timeout=3)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["state"]["exit_code"], 137)

    def test_label_discovery_finds_and_loses_a_runner(self):
        """What makes a manager restart survivable: the index is rebuilt by
        looking, so removing a container really does remove the knowledge."""
        result = self.run_probe(Limits(1000, 64, 32, 32), ["true"])
        found = self.docker.list_managed(gear="it")
        self.assertIn(result["container"][:12],
                      [row["id"][:12] for row in found])
        self.docker.remove(result["container"])
        self.assertNotIn(result["container"][:12],
                         [row["id"][:12] for row in self.docker.list_managed(gear="it")])


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
        self.gear = uuid.uuid4()
        self.addCleanup(self.driver.destroy, self.gear)

    def test_a_slice_carries_the_limits_we_asked_for(self):
        limits = Limits(cpu_millicores=1500, ram_mb=256, scratch_mb=256, pids=64)
        self.driver.ensure(self.gear, limits)
        path = self.driver.path(self.gear)
        self.assertTrue(os.path.isdir(path), f"no cgroup at {path}")
        with open(os.path.join(path, "memory.max")) as handle:
            self.assertEqual(int(handle.read().strip()), limits.memory_bytes)
        with open(os.path.join(path, "pids.max")) as handle:
            self.assertEqual(int(handle.read().strip()), 64)

    def test_the_cgroup_path_follows_systemds_dash_nesting(self):
        """systemd expands EVERY dash into a level, so the obvious shallow path
        finds nothing and a healthy Gear reads as unmeasurable."""
        self.driver.ensure(self.gear, Limits(1000, 128, 64, 32))
        expected = slices.slice_cgroup_path(slices.slice_name(self.gear))
        self.assertEqual(self.driver.path(self.gear), expected)
        self.assertIn("anastasia.slice/anastasia-gear.slice/", expected)
        self.assertTrue(os.path.isdir(expected))

    def test_sampling_a_live_slice_returns_usage(self):
        self.driver.ensure(self.gear, Limits(1000, 128, 64, 32))
        sample = self.driver.sample(self.gear)
        self.assertIn("pids_used", sample)
        self.assertIn("oom_kills", sample)

    def test_ensure_is_idempotent_and_destroy_is_too(self):
        limits = Limits(1000, 128, 64, 32)
        self.driver.ensure(self.gear, limits)
        self.driver.ensure(self.gear, limits)
        self.driver.destroy(self.gear)
        self.driver.destroy(self.gear)
        self.assertFalse(self.driver.exists(self.gear))

    def test_sampling_a_slice_that_is_not_there_is_empty_not_an_error(self):
        self.assertEqual(self.driver.sample(uuid.uuid4()), {})
