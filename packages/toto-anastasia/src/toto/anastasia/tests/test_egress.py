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
DNS_SINK = "10.207.0.3"


def policy(**over) -> egress.Policy:
    base = {"network": "testy_egress_proxy", "bridge": BRIDGE, "subnet": SUBNET,
            "proxy_ip": PROXY_IP, "proxy_port": PROXY_PORT, "dns_sink": DNS_SINK}
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
            "ANASTASIA_EGRESS_DNS": DNS_SINK,
        })
        self.assertTrue(p.configured)
        self.assertEqual(p.proxy_url, f"http://{PROXY_IP}:{PROXY_PORT}")

    def test_a_half_configured_policy_is_OFF_and_says_which_half(self):
        """The dangerous middle. A network with no subnet would install a
        filter for an empty range and then hand out a NIC — the one
        combination this layer exists to prevent."""
        for missing in ("ANASTASIA_EGRESS_SUBNET", "ANASTASIA_EGRESS_PROXY_IP",
                        "ANASTASIA_EGRESS_PROXY_PORT", "ANASTASIA_EGRESS_BRIDGE",
                        "ANASTASIA_EGRESS_DNS"):
            env = {
                "ANASTASIA_EGRESS_NETWORK": "testy_egress_proxy",
                "ANASTASIA_EGRESS_BRIDGE": BRIDGE,
                "ANASTASIA_EGRESS_SUBNET": SUBNET,
                "ANASTASIA_EGRESS_PROXY_IP": PROXY_IP,
                "ANASTASIA_EGRESS_PROXY_PORT": str(PROXY_PORT),
                "ANASTASIA_EGRESS_DNS": DNS_SINK,
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
            "ANASTASIA_EGRESS_DNS": DNS_SINK,
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
        accept = self.text.index(
            f'iifname "{BRIDGE}" ip saddr {PROXY_IP} '
            f'counter name "{netfilter.ACCEPTED_COUNTER}" accept')
        drop = self.text.index(
            f'iifname "{BRIDGE}" counter name "{netfilter.REFUSED_COUNTER}" drop')
        self.assertLess(accept, drop, "the proxy must be exempted before the drop")

    def test_everything_else_off_the_bridge_is_dropped(self):
        self.assertIn(
            f'iifname "{BRIDGE}" counter name "{netfilter.REFUSED_COUNTER}" drop',
            self.text)

    def test_the_counters_are_named_objects_in_the_table(self):
        """Named, so `counters()` can read them by name without parsing rules;
        and declared, because `counter name` on a rule refers to an object
        that must exist or the whole document is rejected by the kernel —
        which would leave a host with no filter and every mount refused."""
        for name in (netfilter.ACCEPTED_COUNTER, netfilter.REFUSED_COUNTER):
            with self.subTest(counter=name):
                self.assertIn(f"counter {name} {{}}", self.rules)

    def test_both_drops_count_into_one_object(self):
        """Refused is refused. A probe at the host and a probe at the internet
        are one claim, and two numbers a reader must add before they mean
        anything are worse than one."""
        drops = [ln for ln in self.rules.splitlines()
                 if ln.strip().endswith(" drop") and "oifname" not in ln]
        self.assertEqual(len(drops), 2, drops)
        for ln in drops:
            self.assertIn(f'counter name "{netfilter.REFUSED_COUNTER}"', ln)

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

    def test_only_the_proxy_PORT_is_reachable_across_the_bridge(self):
        """`proxy_port` was passed by four call sites and read by none, while
        two docstrings claimed "the proxy's address and port are the only
        thing routable". A capsule could open any port on the proxy container,
        and the comment said otherwise — a false reassurance is worse than no
        comment."""
        self.assertIn(
            f'ip daddr {PROXY_IP} tcp dport {PROXY_PORT} accept', self.text)
        self.assertNotIn(f'ip daddr {PROXY_IP} accept', self.rules)

    def test_the_facts_are_interpolated_not_hardcoded(self):
        other = netfilter.ruleset("other0", "172.31.9.0/24", "172.31.9.5", 3128)
        self.assertIn(
            f'iifname "other0" ip saddr 172.31.9.5 '
            f'counter name "{netfilter.ACCEPTED_COUNTER}" accept', other)
        self.assertIn("tcp dport 3128 accept", other)
        self.assertNotIn(BRIDGE, other)


class VerifyTests(SimpleTestCase):
    """What `verify` will and will not accept as proof.

    `mount` trusts this to decide whether a Capsule gets a NIC, so a check that
    passes on a half-installed table is the whole guarantee gone.
    """

    FORWARD_ONLY = (
        'table inet anastasia {\n'
        '  chain forward {\n'
        '    type filter hook forward priority filter - 10; policy accept;\n'
        '    iifname "anastasia-egr0" ct state established,related accept\n'
        '    iifname "anastasia-egr0" ip saddr 10.207.0.2 accept\n'
        '    iifname "anastasia-egr0" drop\n'
        '  }\n'
        '}\n'
    )

    def test_a_missing_input_chain_is_not_proof(self):
        """THE HOLE THIS CLOSES. `iifname "<bridge>" drop` appears in BOTH the
        forward and the input chain, so a whole-table substring search was
        satisfied by a table that had no input chain at all — and the input
        chain is the one that keeps a capsule off this host. Proven by loading
        a forward-only table in a namespace and watching the old check pass."""
        import subprocess
        from unittest import mock

        def fake_nft(*args, check=True):
            # `list chain inet anastasia input` — the chain is absent.
            if args[:2] == ("list", "chain") and args[-1] == "input":
                return subprocess.CompletedProcess(
                    args, 1, "", "Error: No such file or directory")
            return subprocess.CompletedProcess(args, 0, self.FORWARD_ONLY, "")

        with mock.patch.object(netfilter, "_nft", fake_nft):
            with self.assertRaises(netfilter.NetfilterError) as caught:
                netfilter.verify(BRIDGE, SUBNET, PROXY_IP, PROXY_PORT)
        self.assertIn("input", str(caught.exception))
        # ...and the SAME kernel state satisfies a whole-table search, which is
        # why asking per chain is the fix rather than the tidier spelling. This
        # fixture is an earlier draft of our own ruleset: it carried
        # `ct state established,related accept` in FORWARD, so every string the
        # old check looked for was present while `input` did not exist.
        whole_table = " ".join(self.FORWARD_ONLY.split())
        for _family, chain, parts in netfilter._required(BRIDGE, PROXY_IP,
                                                         PROXY_PORT):
            if chain == "input":
                for part in parts:
                    self.assertIn(" ".join(part.split()), whole_table,
                                  "the fixture must satisfy the OLD check, or "
                                  "this test proves nothing about the new one")

    def test_the_chain_header_is_never_asserted(self):
        """nft echoes priorities in its own vocabulary — `priority filter - 10`
        — so a literal `priority -10` check fails against a correct kernel,
        and a test that fails on correct input is a test somebody deletes."""
        for _family, _chain, parts in netfilter._required(BRIDGE, PROXY_IP,
                                                         PROXY_PORT):
            self.assertNotIn("priority", " ".join(parts))
            self.assertNotIn("policy", " ".join(parts))

    def test_a_required_rule_is_matched_on_one_line_not_across_the_chain(self):
        """THE DISARM THIS GUARDS. `_required` is fragments now, because a
        rule carries its counter in the middle; a fragment search over the
        whole chain would be satisfied by three DIFFERENT rules that happen
        to be near each other — which is how a filter with the drop removed
        could still verify green. Planted, as every check here is."""
        parts = (f'iifname "{BRIDGE}"', "drop")
        # The drop removed: only the accept remains.
        disarmed = (f'chain forward {{\n  iifname "{BRIDGE}" ip saddr {PROXY_IP} '
                    f'counter name "x" accept\n}}')
        self.assertFalse(netfilter._line_matching(disarmed, parts))
        # The fragments present, on different lines, for different interfaces.
        split = (f'chain forward {{\n  iifname "{BRIDGE}" accept\n'
                 f'  iifname "other0" drop\n}}')
        self.assertFalse(netfilter._line_matching(split, parts))
        # Out of order is not a match either.
        self.assertFalse(netfilter._line_matching(
            f'iifname "{BRIDGE}" accept ip saddr {PROXY_IP}',
            (f'iifname "{BRIDGE}"', f"ip saddr {PROXY_IP}", "accept")))
        # And what the kernel actually echoes back, counter and all, is one.
        echoed = (f'  iifname "{BRIDGE}" counter packets 3 bytes 180 '
                  f'name "refused" drop')
        self.assertTrue(netfilter._line_matching(echoed, parts))

    def test_counters_are_read_by_name_from_the_kernel(self):
        """`nft -j list counters` answers with objects; the reader picks the
        two it declared and ignores anything else in the table."""
        import json
        import subprocess
        from unittest import mock

        doc = {"nftables": [
            {"metainfo": {"version": "1.1.0"}},
            {"counter": {"family": "inet", "name": netfilter.ACCEPTED_COUNTER,
                         "table": netfilter.TABLE, "packets": 12,
                         "bytes": 34567}},
            {"counter": {"family": "inet", "name": netfilter.REFUSED_COUNTER,
                         "table": netfilter.TABLE, "packets": 3,
                         "bytes": 180}},
            {"counter": {"family": "inet", "name": "somebody_elses",
                         "table": netfilter.TABLE, "packets": 9, "bytes": 9}},
        ]}
        calls = []

        def fake_nft(*args, check=True):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, json.dumps(doc), "")

        with mock.patch.object(netfilter, "_nft", fake_nft):
            out = netfilter.counters()
        self.assertEqual(out, {"egress_bytes": 34567, "egress_packets": 12,
                               "refused_bytes": 180, "refused_packets": 3})
        self.assertIn("-j", calls[0])

    def test_unreadable_counters_are_absent_not_zero(self):
        """A host without nft, without the table or without permission says
        nothing. Zero would be "no capsule has used the internet", which is a
        claim, and not one this reader can make."""
        import subprocess
        from unittest import mock

        def refused(*args, check=True):
            return subprocess.CompletedProcess(args, 1, "", "Operation not permitted")

        with mock.patch.object(netfilter, "_nft", refused):
            self.assertEqual(netfilter.counters(), {})

        def junk(*args, check=True):
            return subprocess.CompletedProcess(args, 0, "not json", "")

        with mock.patch.object(netfilter, "_nft", junk):
            self.assertEqual(netfilter.counters(), {})

    def test_every_chain_that_matters_is_checked(self):
        """A rule renamed in the ruleset and not in `_required` weakens what is
        proven without failing anything, so the two are pinned together."""
        checked = {(f, c) for f, c, _ in netfilter._required(BRIDGE, PROXY_IP,
                                                            PROXY_PORT)}
        self.assertEqual(
            checked, {("inet", "forward"), ("inet", "input"),
                      ("bridge", "forward")})


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

    def test_a_nic_ALWAYS_implies_a_dead_resolver(self):
        """The rule, not one instance of it. A refactor that drops the `--dns`
        flag would quietly reopen the embedded-resolver channel, and nothing
        else in the argv would look different."""
        for network in ("testy_egress_proxy", "some_other_net"):
            with self.subTest(network=network):
                argv = self._argv(network=network, dns=DNS_SINK)
                self.assertIn("--dns", argv,
                              "a runner with a network must have its resolver "
                              "pointed somewhere dead")

    def test_the_resolver_sink_is_not_a_loopback_address(self):
        """moby dials a LOOPBACK upstream from the daemon's namespace, so
        `--dns 127.0.0.1` means "whatever answers on the HOST's loopback" —
        often a real resolver. A non-loopback address is dialled from inside
        the container, where the packet filter refuses it."""
        import ipaddress
        self.assertFalse(ipaddress.ip_address(DNS_SINK).is_loopback)

    def test_an_egress_runner_gets_a_resolver_that_does_not_work(self):
        """Docker's embedded resolver forwards from the DAEMON's side, so the
        query leaves as dockerd's packet and no rule in netfilter can see it.
        Names spelled into subdomains would walk straight past the filter."""
        argv = self._argv(network="testy_egress_proxy", dns=DNS_SINK)
        self.assertEqual(argv[argv.index("--dns") + 1], DNS_SINK)
