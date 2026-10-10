"""Network access evidence. Failed and pending connects are attempts only.

Loopback, DNS, and Unix sockets require explicit allowances. A zero-return
connect is OS-level access evidence, not proof of data receipt. UDP connect
can succeed without reaching a peer; receiver-side evidence is needed for
network effects and nonblocking connection completion.
"""

from __future__ import annotations

import re
import ast
from dataclasses import dataclass
from .strace import calls

# strace formats:
#   1234  connect(5, {sa_family=AF_INET,  sin_port=htons(80),  sin_addr=inet_addr("1.2.3.4")},  16) = 0
#   1234  connect(5, {sa_family=AF_INET6, sin6_port=htons(443), sin6_addr=inet_pton(AF_INET6, "::1")}, 28) = 0
_INET_RE = re.compile(
    r'connect\(\d+(?:<.*?>)?,\s*\{sa_family=AF_INET,\s*sin_port=htons\((\d+)\),\s*sin_addr=inet_addr\("([^"]+)"\)'
)
_INET6_RE = re.compile(
    r'connect\(\d+(?:<.*?>)?,\s*\{sa_family=AF_INET6,[^}]*?sin6_port=htons\((\d+)\),'
    r'[^}]*?(?:sin6_addr=)?(?:inet_pton\(AF_INET6,\s*)?"([^"]+)"'
)
_UNIX_RE = re.compile(
    r'connect\(\d+(?:<.*?>)?,\s*\{sa_family=AF_UNIX,\s*sun_path=(@?"(?:[^"\\]|\\.)*")'
)



@dataclass(frozen=True)
class NetCanaryConfig:
    allowed_ips: list[str]
    skip_ports: tuple[int, ...] = ()
    allowed_endpoints: tuple[tuple[str, int], ...] = ()
    allowed_unix_paths: tuple[str, ...] = ()


def parse_connects(strace_output: str, *, successful_only: bool = True) -> list[tuple[str, int]]:
    """Return completed connects by default, or all attempts when requested.

    EINPROGRESS is not a completed connection. AF_UNIX uses port 0 and a
    unix: prefix so an IP allowance cannot authorize an IPC endpoint.
    """
    out = []
    for _, body, result in calls(strace_output):
        if successful_only and result != 0:
            continue
        match = _INET_RE.search(body) or _INET6_RE.search(body)
        if match:
            out.append((match[2], int(match[1])))
        else:
            match = _UNIX_RE.search(body)
            if match:
                value = match[1]
                abstract = value.startswith("@")
                try:
                    path = ast.literal_eval(value[1:] if abstract else value)
                except (SyntaxError, ValueError):
                    continue
                out.append(("unix:" + ("@" if abstract else "") + path, 0))
    return out


def check_canary(connects: list[tuple[str, int]], cfg: NetCanaryConfig) -> list[dict]:
    """One trigger per endpoint not explicitly allowed by the lab profile."""
    seen = set()
    triggers = []
    for addr, port in connects:
        if (addr, port) in seen:
            continue
        seen.add((addr, port))
        if (addr, port) in cfg.allowed_endpoints:
            continue
        if addr.startswith("unix:"):
            if addr[5:] in cfg.allowed_unix_paths:
                continue
        elif port in cfg.skip_ports or addr in cfg.allowed_ips:
            continue
        triggers.append({"vector": "network", "evidence": f"{addr}:{port}"})
    return triggers
