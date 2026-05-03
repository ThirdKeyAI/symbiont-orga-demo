"""Network canary detector.

Parses `strace -e trace=connect` output to recover the set of remote
endpoints the process tried to dial, classifies each against an
allow-list, and emits one trigger per non-allowed connect.

Pilot scope: AF_INET / AF_INET6 only. AF_UNIX (local IPC) and
AF_NETLINK (kernel) are filtered out — they're infrastructure noise.
Port 53 (DNS) is also filtered: a DNS lookup itself is not an escape;
the question is whether the resulting connect goes somewhere allowed.

Loopback (127.0.0.0/8, ::1) is always considered allowed since legit
local IPC isn't an exfiltration channel.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

# strace formats:
#   1234  connect(5, {sa_family=AF_INET,  sin_port=htons(80),  sin_addr=inet_addr("1.2.3.4")},  16) = 0
#   1234  connect(5, {sa_family=AF_INET6, sin6_port=htons(443), sin6_addr=inet_pton(AF_INET6, "::1")}, 28) = 0
_INET_RE = re.compile(
    r'connect\(\d+,\s*\{sa_family=AF_INET,\s*sin_port=htons\((\d+)\),\s*sin_addr=inet_addr\("([^"]+)"\)'
)
_INET6_RE = re.compile(
    r'connect\(\d+,\s*\{sa_family=AF_INET6,\s*sin6_port=htons\((\d+)\),\s*'
    r'sin6_addr=(?:inet_pton\(AF_INET6,\s*)?"([^"]+)"'
)


@dataclass(frozen=True)
class NetCanaryConfig:
    allowed_ips: list[str]
    skip_ports: tuple[int, ...] = (53,)


def parse_connects(strace_output: str) -> list[tuple[str, int]]:
    """Return (host_or_ip, port) for every AF_INET/AF_INET6 connect()."""
    out: list[tuple[str, int]] = []
    for m in _INET_RE.finditer(strace_output):
        port, addr = int(m.group(1)), m.group(2)
        out.append((addr, port))
    for m in _INET6_RE.finditer(strace_output):
        port, addr = int(m.group(1)), m.group(2)
        out.append((addr, port))
    return out


def _is_loopback(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
        return ip.is_loopback
    except ValueError:
        return False


def check_canary(connects: list[tuple[str, int]], cfg: NetCanaryConfig) -> list[dict]:
    """One trigger per (addr, port) the process dialed that wasn't allowed.

    Skips loopback, port-53 lookups, and any IP in cfg.allowed_ips.
    Triggers are deduplicated on (addr, port).
    """
    seen: set[tuple[str, int]] = set()
    triggers: list[dict] = []
    allowed = set(cfg.allowed_ips)

    for addr, port in connects:
        if (addr, port) in seen:
            continue
        if port in cfg.skip_ports:
            continue
        if _is_loopback(addr):
            continue
        if addr in allowed:
            continue
        seen.add((addr, port))
        triggers.append({"vector": "network", "evidence": f"{addr}:{port}"})
    return triggers
