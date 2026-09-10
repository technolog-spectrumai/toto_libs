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

BRIDGE = "anastasia-egr0"
SUBNET = "10.207.0.0/24"
PROXY_IP = "10.207.0.2"
PROXY_PORT = 4750


def policy(**over) -> egress.Policy:
    base = {"network": "testy_egress_proxy", "bridge": BRIDGE, "subnet": SUBNET,
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
            "ANASTASIA_EGRESS_BRIDGE": BRIDGE,
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
                        "ANASTASIA_EGRESS_PROXY_PORT", "ANASTASIA_EGRESS_BRIDGE"):
            env = {
                "ANASTASIA_EGRESS_NETWORK": "testy_egress_proxy",
                "ANASTASIA_EGRESS_BRIDGE": BRIDGE,
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
            "ANASTASIA_EGRESS_NETWORK": "n", "ANASTASIA_EGRESS_BRIDGE": BRIDGE,
            "ANASTASIA_EGRESS_SUBNET": SUBNET,
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
    """What the kernel is asked to enforce, asserted on the text.

    Whether the kernel ACCEPTS this text is a provisioning probe, not something
    a unit test can honestly claim — but `nft -c -f` was run against it by hand
    and the first draft was rejected outright, which is why the interface rules
    below are asserted so exactly.
    """

    def setUp(self):
        self.text = netfilter.ruleset(BRIDGE, SUBNET, PROXY_IP, PROXY_PORT)
        # INSTRUCTIONS ONLY, for the assertions that forbid something. The
        # ruleset's comments explain what `policy drop` and `iif "br-*"` would
        # do, and a rule reading the prose would fail on its own explanation —
        # the same trap the egress Dockerfile's test records.
        self.rules = "\n".join(
            ln for ln in self.text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#"))

    def test_the_proxy_may_reach_the_internet(self):
        """FIRST, and the first draft of this file omitted it: the proxy's own
        address is inside the subnet, so a blanket drop killed the proxy's
        outbound connections and nothing could ever be fetched."""
        accept = self.text.index(f'iifname "{BRIDGE}" ip saddr {PROXY_IP} accept')
        drop = self.text.index(f'iifname "{BRIDGE}" drop')
        self.assertLess(accept, drop, "the proxy must be exempted before the drop")

    def test_everything_else_off_the_bridge_is_dropped(self):
        self.assertIn(f'iifname "{BRIDGE}" drop', self.text)

    def test_matching_is_on_the_interface_not_the_source_address(self):
        """A source address is a field the sender fills in; an interface is
        not something inside the container can choose."""
        self.assertNotIn(f"ip saddr {SUBNET} drop", self.rules)
        self.assertIn(f'iifname "{BRIDGE}"', self.rules)

    def test_there_is_an_input_chain_for_the_host_itself(self):
        """Host-destined packets are delivered locally and never traverse the
        forward hook. A filter with only a forward chain stops a capsule
        reaching the internet and leaves it able to reach everything the host
        runs."""
        self.assertIn("hook input", self.text)

    def test_no_wildcard_interface_is_ever_written(self):
        """nft has no wildcard for `iif`; an invalid one makes the kernel
        reject the ENTIRE ruleset, so the filter silently filters nothing.
        This exact mistake shipped in the first draft."""
        self.assertNotIn('iif "', self.rules)
        self.assertNotIn("br-*", self.rules)

    def test_the_forward_policy_is_accept(self):
        """A `policy drop` on a forward hook drops every forwarded packet on
        the host — every other container, every other stack — the moment this
        table loads."""
        self.assertIn("policy accept", self.rules)
        self.assertNotIn("policy drop", self.rules)

    def test_capsule_to_capsule_is_filtered_in_the_bridge_family(self):
        """Same-subnet traffic is SWITCHED, never routed, so it never reaches
        either IP chain. Docker's own enable_icc=false would stop it and
        cannot be used — the proxy is a container on that bridge too."""
        self.assertIn("table bridge anastasia", self.text)
        self.assertIn(f'iifname "{BRIDGE}" oifname "{BRIDGE}" drop', self.text)

    def test_the_bridge_rules_are_scoped_to_this_bridge_on_both_sides(self):
        """An unscoped rule in the bridge family applies to EVERY bridge on
        the host, which would filter other stacks' networks as a side effect
        of turning egress on for this one."""
        for line in self.text.splitlines():
            body = line.strip()
            if body.endswith(("accept", "drop")) and "oifname" in body:
                self.assertIn(f'iifname "{BRIDGE}"', body)
                self.assertIn(f'oifname "{BRIDGE}"', body)

    def test_the_table_is_replaced_rather_than_appended(self):
        """`nft -f` ADDS to an existing table, so without the create-then-
        delete preamble every restart leaves a second copy of every rule."""
        self.assertIn("delete table inet anastasia", self.text)
        self.assertIn("delete table bridge anastasia", self.text)

    def test_the_facts_are_interpolated_not_hardcoded(self):
        other = netfilter.ruleset("other0", "172.31.9.0/24", "172.31.9.5", 3128)
        self.assertIn('iifname "other0" ip saddr 172.31.9.5 accept', other)
        self.assertNotIn(BRIDGE, other)


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
