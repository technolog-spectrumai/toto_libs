"""Controlled internet access: the policy, the filter, and what a runner gets.

THE CLAIM UNDER TEST is not "a Capsule can reach the internet". It is the much
narrower one this feature actually makes: a Capsule that opted in reaches the
PROXY and nothing else, a Capsule that did not keeps `--network none`, and a
host that cannot filter hands out no NIC at all.

No Django here beyond the settings the suite already builds, and no root: the
netfilter tests assert on the ruleset TEXT, which is where the rules that matter
are decided. Whether the kernel accepts that text is a provisioning probe
(`deploy/anastasia/PROBES.md`), not something a unit test can honestly claim.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from toto.anastasia.executor import egress, netfilter
from toto.anastasia.families import PDF
from toto.anastasia.limits import Limits

SUBNET = "10.207.0.0/24"
PROXY_IP = "10.207.0.2"
PROXY_PORT = 4750


def policy(**over) -> egress.Policy:
    base = {"network": "testy_egress_proxy", "subnet": SUBNET,
            "proxy_ip": PROXY_IP, "proxy_port": PROXY_PORT}
    base.update(over)
    return egress.Policy(**base)


class PolicyTests(SimpleTestCase):
    def test_a_host_that_says_nothing_offers_no_egress(self):
        empty = egress.from_environ({})
        self.assertFalse(empty.configured)
        self.assertIn("not configured", empty.describe())

    def test_a_fully_stated_policy_is_configured(self):
        p = egress.from_environ({
            "ANASTASIA_EGRESS_NETWORK": "testy_egress_proxy",
            "ANASTASIA_EGRESS_SUBNET": SUBNET,
            "ANASTASIA_EGRESS_PROXY_IP": PROXY_IP,
            "ANASTASIA_EGRESS_PROXY_PORT": str(PROXY_PORT),
        })
        self.assertTrue(p.configured)
        self.assertEqual(p.proxy_url, f"http://{PROXY_IP}:{PROXY_PORT}")

    def test_a_half_configured_policy_is_OFF_and_says_which_half(self):
        """The dangerous middle. A network with no subnet would install a
        filter for an empty range and then hand out a NIC — the one
        combination this layer exists to prevent."""
        for missing in ("ANASTASIA_EGRESS_SUBNET", "ANASTASIA_EGRESS_PROXY_IP",
                        "ANASTASIA_EGRESS_PROXY_PORT"):
            env = {
                "ANASTASIA_EGRESS_NETWORK": "testy_egress_proxy",
                "ANASTASIA_EGRESS_SUBNET": SUBNET,
                "ANASTASIA_EGRESS_PROXY_IP": PROXY_IP,
                "ANASTASIA_EGRESS_PROXY_PORT": str(PROXY_PORT),
            }
            del env[missing]
            with self.subTest(missing=missing):
                p = egress.from_environ(env)
                self.assertFalse(p.configured)
                self.assertIn(missing, p.describe())

    def test_an_unparseable_port_is_absent_not_fatal(self):
        p = egress.from_environ({
            "ANASTASIA_EGRESS_NETWORK": "n", "ANASTASIA_EGRESS_SUBNET": SUBNET,
            "ANASTASIA_EGRESS_PROXY_IP": PROXY_IP,
            "ANASTASIA_EGRESS_PROXY_PORT": "four thousand",
        })
        self.assertFalse(p.configured)

    def test_the_proxy_variables_are_set_in_both_cases(self):
        """A workload reading the case we did not set makes its own direct
        connection — refused by the filter, so safe, but it looks like a
        network fault rather than a missing variable."""
        env = egress.runner_environment(policy())
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            self.assertEqual(env[name], f"http://{PROXY_IP}:{PROXY_PORT}")

    def test_no_proxy_is_empty_rather_than_unset(self):
        """Some clients default NO_PROXY to localhost and the link-local
        ranges, and an exemption list is the one thing that could route a
        request around the proxy."""
        env = egress.runner_environment(policy())
        self.assertEqual(env["NO_PROXY"], "")
        self.assertEqual(env["no_proxy"], "")

    def test_an_unconfigured_policy_sets_no_variables_at_all(self):
        self.assertEqual(egress.runner_environment(egress.Policy()), {})


class RulesetTests(SimpleTestCase):
    """What the kernel is asked to enforce, asserted on the text."""

    def setUp(self):
        self.text = netfilter.ruleset(SUBNET, PROXY_IP, PROXY_PORT)

    def test_the_only_accepted_destination_is_the_proxy_address_and_port(self):
        self.assertIn(
            f"ip saddr {SUBNET} ip daddr {PROXY_IP} tcp dport {PROXY_PORT} accept",
            self.text)

    def test_everything_else_from_the_subnet_is_dropped(self):
        """The rule that makes the proxy compulsory rather than advisory: a
        raw socket, DNS to the world, the metadata address and another stack's
        database all land here."""
        self.assertIn(f"ip saddr {SUBNET} drop", self.text)

    def test_the_accept_precedes_the_drop(self):
        """Order is the whole ruleset. A drop evaluated first refuses the
        proxy too, and the feature is dead rather than insecure — but it would
        be dead in a way that invites somebody to 'fix' it by reordering
        without understanding which order was wrong."""
        accept = self.text.index("tcp dport")
        drop = self.text.index(f"ip saddr {SUBNET} drop")
        self.assertLess(accept, drop)

    def test_the_forward_policy_is_accept(self):
        """A `policy drop` on a forward hook drops every forwarded packet on
        the host — every other container, every other stack — the moment this
        table loads. The drops are scoped to the egress subnet instead."""
        self.assertIn("policy accept", self.text)
        self.assertNotIn("policy drop", self.text)

    def test_capsule_to_capsule_is_filtered_in_the_bridge_family(self):
        """Same-subnet traffic is BRIDGED, never routed, so it never reaches
        the forward hook and an L3 drop never sees it. Docker's own
        enable_icc=false would stop it and cannot be used — the proxy is a
        container on that bridge too."""
        self.assertIn("table bridge anastasia", self.text)
        self.assertIn(f"ip saddr {SUBNET} ip daddr {SUBNET} drop", self.text)

    def test_the_proxy_is_reachable_across_the_bridge(self):
        self.assertIn(f"ip daddr {PROXY_IP} accept", self.text)

    def test_return_traffic_is_accepted(self):
        self.assertIn("ct state established,related accept", self.text)

    def test_the_subnet_and_proxy_are_interpolated_not_hardcoded(self):
        """The executor is told these by the deploy tool that pinned them on
        the Docker network. A ruleset with its own idea of the subnet would
        filter a range nothing is on."""
        other = netfilter.ruleset("172.31.9.0/24", "172.31.9.5", 3128)
        self.assertIn("ip saddr 172.31.9.0/24 ip daddr 172.31.9.5 "
                      "tcp dport 3128 accept", other)
        self.assertNotIn(SUBNET, other)


class RunnerPostureTests(SimpleTestCase):
    """What actually reaches `docker run`."""

    def _argv(self, **over):
        from toto.anastasia.executor.drivers import docker as drv
        client = drv.DockerClient()
        kwargs = dict(
            family=PDF, limits=Limits(cpu_millicores=500, ram_mb=128,
                                      scratch_mb=64, pids=32),
            name="anastasia-test", cgroup_parent=None,
            input_dir="/tmp/in", output_dir="/tmp/out",
            env={}, labels={}, argv=["true"], network=None, dns=None)
        kwargs.update(over)
        return client.build_run_args(**kwargs)

    def test_without_egress_a_runner_still_gets_no_network(self):
        argv = self._argv()
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertNotIn("--dns", argv)

    def test_with_egress_a_runner_joins_the_proxy_network(self):
        argv = self._argv(network="testy_egress_proxy")
        self.assertEqual(argv[argv.index("--network") + 1], "testy_egress_proxy")

    def test_an_egress_runner_gets_a_resolver_that_does_not_work(self):
        """Docker's embedded resolver forwards from the DAEMON's side, so the
        query leaves as dockerd's packet and no rule in netfilter can see it.
        Names spelled into subdomains would walk straight past the filter."""
        argv = self._argv(network="testy_egress_proxy",
                          dns=egress.RUNNER_DNS)
        self.assertEqual(argv[argv.index("--dns") + 1], "127.0.0.1")
