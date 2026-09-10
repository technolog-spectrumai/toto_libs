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
    subnet: str = ""
    proxy_ip: str = ""
    proxy_port: int = 0

    @property
    def configured(self) -> bool:
        """Whether this host offers egress at all.

        EVERY FIELD, not just the network name. A half-configured policy — a
        network with no subnet, say — would install a filter for an empty range
        and then hand out a NIC, which is the one combination this whole layer
        exists to prevent. Partial configuration is treated as no
        configuration, and `describe` says which field is missing.
        """
        return bool(self.network and self.subnet
                    and self.proxy_ip and self.proxy_port)

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
            ("ANASTASIA_EGRESS_SUBNET", self.subnet),
            ("ANASTASIA_EGRESS_PROXY_IP", self.proxy_ip),
            ("ANASTASIA_EGRESS_PROXY_PORT", self.proxy_port),
        ) if not value]
        if len(missing) == 4:
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
        subnet=(env.get("ANASTASIA_EGRESS_SUBNET", "") or "").strip(),
        proxy_ip=(env.get("ANASTASIA_EGRESS_PROXY_IP", "") or "").strip(),
        proxy_port=port,
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


#: What a runner with egress is given for name resolution: nothing that works.
#:
#: THE CHANNEL THIS CLOSES. On a user-defined network Docker puts its embedded
#: resolver at 127.0.0.11 in the container's `resolv.conf`. That server runs in
#: the container's namespace but forwards what it cannot answer from the
#: DAEMON's side — so the query leaves the host as dockerd's packet, never the
#: container's, and no rule in `netfilter` can see it. A capsule could spell
#: data into subdomains and read it off an authoritative server.
#:
#: Pointing the upstream at the container's own loopback, where nothing
#: listens, makes external resolution fail. A workload with egress does not
#: need it: it sends `CONNECT host:443` to the proxy BY ADDRESS and Smokescreen
#: does the resolving — which is also what makes the allowlist enforceable,
#: since the name is checked by the thing that resolves it.
RUNNER_DNS = "127.0.0.1"
