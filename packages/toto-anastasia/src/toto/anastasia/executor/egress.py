"""What a Capsule that opted in to the internet is actually given.

ONE PLACE THAT KNOWS THE SHAPE OF EGRESS, because four things need it and none
of them may disagree: the startup that installs the packet filter, the mount
that refuses a Capsule it cannot filter, the runner argv that joins the network,
and the environment a workload reads to find the proxy. When those drifted apart
in the kernel-network era the symptom was every job dying at start with "network
not found" — two callers computing one name differently.

READ FROM THE ENVIRONMENT, never inferred. The deploy tool writes the network's
name, subnet and proxy address into the executor's EnvironmentFile because it is
the same tool that created the Docker network with those exact values pinned. An
executor that guessed a subnet would write a filter for a range nothing is on.

OFF IS THE DEFAULT AND OFF IS SAFE. A host whose config never mentioned egress
has `ANASTASIA_EGRESS_NETWORK` unset, `configured()` is False, no filter is
installed, and every runner keeps `--network none`. Nothing about this module
changes what an existing deployment does.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    """The egress facts, or an empty policy when the host has none."""

    network: str = ""
    #: The bridge interface, pinned by the deploy tool. The nftables rules
    #: match on it rather than on a source address, because an interface is
    #: not something a workload can forge.
    bridge: str = ""
    subnet: str = ""
    proxy_ip: str = ""
    proxy_port: int = 0
    #: Where a runner's DNS is pointed: an address on this bridge that nothing
    #: answers on. See the module footer for why it must not be a loopback.
    dns_sink: str = ""

    @property
    def configured(self) -> bool:
        """Whether this host offers egress at all.

        EVERY FIELD, not just the network name. A half-configured policy — a
        network with no subnet, say — would install a filter for an empty range
        and then hand out a NIC, which is the one combination this whole layer
        exists to prevent. Partial configuration is treated as no
        configuration, and `describe` says which field is missing.
        """
        return bool(self.network and self.bridge and self.subnet
                    and self.proxy_ip and self.proxy_port and self.dns_sink)

    @property
    def proxy_url(self) -> str:
        """What a workload puts in ``HTTPS_PROXY``."""
        return f"http://{self.proxy_ip}:{self.proxy_port}"

    def describe(self) -> str:
        if self.configured:
            return (f"egress via {self.proxy_url} on {self.network} "
                    f"({self.subnet})")
        missing = [name for name, value in (
            ("ANASTASIA_EGRESS_NETWORK", self.network),
            ("ANASTASIA_EGRESS_BRIDGE", self.bridge),
            ("ANASTASIA_EGRESS_SUBNET", self.subnet),
            ("ANASTASIA_EGRESS_PROXY_IP", self.proxy_ip),
            ("ANASTASIA_EGRESS_PROXY_PORT", self.proxy_port),
            ("ANASTASIA_EGRESS_DNS", self.dns_sink),
        ) if not value]
        if len(missing) == 6:
            return "egress is not configured on this host"
        return ("egress is only half configured and is therefore OFF; "
                f"missing: {', '.join(missing)}")


def from_environ(env=None) -> Policy:
    """The policy this executor was started with."""
    env = os.environ if env is None else env
    try:
        port = int(env.get("ANASTASIA_EGRESS_PROXY_PORT", "0") or 0)
    except ValueError:
        # A port that will not parse is not a port. Treated as absent so
        # `configured` is False and the host offers no egress, rather than
        # crashing a daemon that would otherwise run every batch job fine.
        port = 0
    return Policy(
        network=(env.get("ANASTASIA_EGRESS_NETWORK", "") or "").strip(),
        bridge=(env.get("ANASTASIA_EGRESS_BRIDGE", "") or "").strip(),
        subnet=(env.get("ANASTASIA_EGRESS_SUBNET", "") or "").strip(),
        proxy_ip=(env.get("ANASTASIA_EGRESS_PROXY_IP", "") or "").strip(),
        proxy_port=port,
        dns_sink=(env.get("ANASTASIA_EGRESS_DNS", "") or "").strip(),
    )


def runner_environment(policy: Policy) -> dict:
    """The proxy variables a runner is given, when it has egress.

    BOTH CASES of each name. `requests`, `pip`, `curl` and Go's net/http read
    the lowercase forms; a good deal of Java and .NET reads the uppercase. A
    workload that reads the one we did not set makes its own direct connection
    — which the packet filter refuses, so the failure is safe but looks like a
    network fault rather than a missing variable.

    NO_PROXY is deliberately EMPTY rather than unset. A default in some clients
    exempts `localhost` and the link-local ranges, and an exemption list is the
    one thing that could route a request around the proxy.
    """
    if not policy.configured:
        return {}
    url = policy.proxy_url
    return {
        "HTTP_PROXY": url, "http_proxy": url,
        "HTTPS_PROXY": url, "https_proxy": url,
        "NO_PROXY": "", "no_proxy": "",
    }


#: WHY A RUNNER'S DNS IS POINTED AT A DEAD ADDRESS ON ITS OWN BRIDGE.
#:
#: THE CHANNEL THIS CLOSES. On a user-defined network Docker puts its embedded
#: resolver at 127.0.0.11 in the container's `resolv.conf`, and `--dns` sets
#: that resolver's UPSTREAM. Left alone, a query the resolver cannot answer is
#: forwarded to the host's real DNS — so a capsule could spell data into
#: subdomains and read it off an authoritative server, and no rule in
#: `netfilter` would see it, because the packet leaves as dockerd's and not as
#: the container's.
#:
#: WHY NOT 127.0.0.1, which is the obvious choice and is wrong. moby marks a
#: loopback upstream `HostLoopback` and dials it from the DAEMON's namespace —
#: the branch exists so a host running systemd-resolved on 127.0.0.53 can serve
#: its containers. `--dns 127.0.0.1` therefore means "whatever answers on the
#: HOST's loopback", which on many hosts is a working resolver. It reads like
#: closing the channel and on those hosts leaves it open.
#:
#: A non-loopback address is dialled from INSIDE the container's namespace, so
#: the query becomes an ordinary packet on the egress bridge to something that
#: is not the proxy — and the nftables drop refuses it. The failure is enforced
#: by the filter that is supposed to enforce it, and shows in its counters.
#:
#: A workload with egress does not need working DNS: it sends
#: `CONNECT host:443` to the proxy BY ADDRESS and Smokescreen resolves — which
#: is also what makes the allowlist enforceable, since the thing checking the
#: name is the thing looking it up.
#:
#: WHAT REMAINS. The embedded resolver still answers for container NAMES on the
#: egress network. That is harmless while the proxy is the only other member,
#: and it is why nothing else is ever placed on this network.
