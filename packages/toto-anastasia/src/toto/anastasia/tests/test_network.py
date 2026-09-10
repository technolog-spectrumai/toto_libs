"""Internet bytes per Capsule: read off the runner, kept past the runner.

Unit-level, against files and fakes. The claim that matters is the one in
`network.py`'s header: a runner's counters die with it, so a naive reading
would show a capsule's usage falling to zero every time a job ended. These
tests prove the total only ever rises within a mount — and that a failure to
read is reported as nothing, never as zero.
"""

from __future__ import annotations

import os
import tempfile

from django.test import SimpleTestCase

from toto.anastasia.executor import network

_HEADER = ("Inter-|   Receive                                                "
           "|  Transmit\n"
           " face |bytes    packets errs drop fifo frame compressed multicast"
           "|bytes    packets errs drop fifo colls carrier compressed\n")


def _dev_line(name, rx, tx):
    return (f"{name:>6}: {rx:8d} {rx // 100:8d}    0    0    0     0          0"
            f"         0 {tx:8d} {tx // 100:8d}    0    0    0     0       0"
            f"          0\n")


class ProcNetDevTests(SimpleTestCase):
    """`/proc/<pid>/net/dev`, parsed."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _write(self, pid, body):
        os.makedirs(os.path.join(self.root, str(pid), "net"), exist_ok=True)
        with open(os.path.join(self.root, str(pid), "net", "dev"), "w") as fh:
            fh.write(body)

    def test_it_sums_every_interface_but_loopback(self):
        self._write(41, _HEADER + _dev_line("lo", 9999, 9999)
                    + _dev_line("eth0", 1000, 200) + _dev_line("eth1", 10, 1))
        out = network.read_namespace_bytes(41, proc_root=self.root)
        self.assertEqual(out, {"rx_bytes": 1010, "tx_bytes": 201})

    def test_a_namespace_that_is_gone_is_none_not_zero(self):
        """The runner exited between `list_managed` and this read. "We could
        not look" and "it moved nothing" are different claims."""
        self.assertIsNone(network.read_namespace_bytes(12345, proc_root=self.root))

    def test_a_namespace_with_only_loopback_is_none(self):
        self._write(42, _HEADER + _dev_line("lo", 5, 5))
        self.assertIsNone(network.read_namespace_bytes(42, proc_root=self.root))

    def test_junk_is_none(self):
        self._write(43, "not a proc file\n")
        self.assertIsNone(network.read_namespace_bytes(43, proc_root=self.root))


class _Driver:
    """A driver that knows each runner's pid, and can be made to forget."""

    def __init__(self, pids):
        self.pids = dict(pids)

    def container_pid(self, cid):
        return self.pids.get(cid, 0)


def _row(cid, state="running"):
    return {"id": cid, "state": state, "capsule": "c", "execution": cid}


class NetworkMeterTests(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.meter = network.NetworkMeter(proc_root=self.root)

    def _runner(self, pid, rx, tx):
        os.makedirs(os.path.join(self.root, str(pid), "net"), exist_ok=True)
        with open(os.path.join(self.root, str(pid), "net", "dev"), "w") as fh:
            fh.write(_HEADER + _dev_line("eth0", rx, tx))

    def test_a_live_runner_is_read(self):
        self._runner(100, 500, 50)
        out = self.meter.sample(_Driver({"r1": 100}), "c", [_row("r1")])
        self.assertEqual(out, {"net_rx_bytes": 500, "net_tx_bytes": 50})

    def test_the_total_survives_the_runner(self):
        """THE TEST THIS MODULE EXISTS FOR. The job ends, the container is
        removed, its counters go with it — and the capsule's total does not
        fall back to zero."""
        driver = _Driver({"r1": 100, "r2": 200})
        self._runner(100, 500, 50)
        self.meter.sample(driver, "c", [_row("r1")])
        # r1 is gone; r2 has started and has moved a little.
        self._runner(200, 40, 4)
        out = self.meter.sample(driver, "c", [_row("r2")])
        self.assertEqual(out, {"net_rx_bytes": 540, "net_tx_bytes": 54})

    def test_the_total_only_rises_within_a_mount(self):
        driver = _Driver({"r1": 100, "r2": 200, "r3": 300})
        seen = []
        self._runner(100, 100, 10)
        seen.append(self.meter.sample(driver, "c", [_row("r1")])["net_rx_bytes"])
        self._runner(100, 300, 30)
        seen.append(self.meter.sample(driver, "c", [_row("r1")])["net_rx_bytes"])
        self._runner(200, 5, 1)
        seen.append(self.meter.sample(driver, "c", [_row("r2")])["net_rx_bytes"])
        seen.append(self.meter.sample(driver, "c", [])["net_rx_bytes"])
        self._runner(300, 1, 1)
        seen.append(self.meter.sample(driver, "c", [_row("r3")])["net_rx_bytes"])
        self.assertEqual(seen, sorted(seen), seen)

    def test_a_stopped_runner_is_not_read(self):
        """No namespace to read, and reading a stale pid could read a
        namespace that now belongs to something else entirely."""
        self._runner(100, 500, 50)
        out = self.meter.sample(_Driver({"r1": 100}), "c",
                                [_row("r1", state="exited")])
        self.assertIsNone(out)

    def test_nothing_ever_measured_is_none(self):
        """A capsule with egress that has never run a job has no reading, and
        must not be drawn as a flat zero."""
        self.assertIsNone(self.meter.sample(_Driver({}), "c", []))

    def test_forgetting_resets_the_capsule(self):
        """A remount is a new machine. Carrying the total across it would
        attribute one reservation's traffic to the next."""
        self._runner(100, 500, 50)
        driver = _Driver({"r1": 100})
        self.meter.sample(driver, "c", [_row("r1")])
        self.meter.forget("c")
        self.assertIsNone(self.meter.sample(driver, "c", []))

    def test_capsules_are_metered_separately(self):
        self._runner(100, 500, 50)
        self._runner(200, 7, 7)
        driver = _Driver({"r1": 100, "r2": 200})
        a = self.meter.sample(driver, "a", [_row("r1")])
        b = self.meter.sample(driver, "b", [_row("r2")])
        self.assertEqual(a["net_rx_bytes"], 500)
        self.assertEqual(b["net_rx_bytes"], 7)

    def test_a_driver_that_cannot_answer_costs_nothing(self):
        class Broken:
            def container_pid(self, cid):
                raise RuntimeError("daemon down")

        self.assertIsNone(self.meter.sample(Broken(), "c", [_row("r1")]))


class ContainerPidTests(SimpleTestCase):
    """The driver seam: `State.Pid` without the caller parsing inspect."""

    def _client(self, stdout, rc=0):
        import subprocess
        from unittest import mock

        from toto.anastasia.executor.drivers.docker import DockerClient

        client = DockerClient()
        client._run = mock.Mock(return_value=subprocess.CompletedProcess(
            [], rc, stdout, ""))
        return client

    def test_it_reads_the_pid(self):
        client = self._client('[{"State": {"Pid": 4242, "Running": true}}]')
        self.assertEqual(client.container_pid("abc"), 4242)

    def test_a_stopped_container_is_zero(self):
        client = self._client('[{"State": {"Pid": 0, "Running": false}}]')
        self.assertEqual(client.container_pid("abc"), 0)

    def test_a_missing_container_is_zero(self):
        client = self._client("", rc=1)
        self.assertEqual(client.container_pid("abc"), 0)
