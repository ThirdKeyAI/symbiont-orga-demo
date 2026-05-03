"""Filesystem canary detector.

Parses `strace -f -e trace=openat` output to recover the full set of
filesystem paths a process touched, then matches that set against a
canary configuration (exact paths and glob patterns). Substrate-agnostic:
the same detector consumes traces from any harness that ran its child
under strace.

Pilot uses strace because it's universally available and zero-setup;
eBPF is the long-term lower-overhead replacement.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Iterable

# Match the path argument of any openat(...) syscall, including the
# `<unfinished ...>` form that strace -f emits when interleaving threads.
_OPENAT_RE = re.compile(r'openat\([^,]+,\s*"([^"]+)"')


def parse_strace_paths(strace_output: str) -> list[str]:
    """Return every path appearing as the second arg to openat()."""
    return _OPENAT_RE.findall(strace_output)


@dataclass(frozen=True)
class FsCanaryConfig:
    paths: list[str]
    path_globs: list[str]


def check_canary(observed_paths: Iterable[str], cfg: FsCanaryConfig) -> list[dict]:
    """Return one trigger record per canary path the process touched.

    Triggers are deduplicated on the path. Order is the order of first
    appearance in `observed_paths`.
    """
    seen: set[str] = set()
    triggers: list[dict] = []
    canary_set = set(cfg.paths)

    for path in observed_paths:
        hit = path in canary_set or any(fnmatch.fnmatch(path, g) for g in cfg.path_globs)
        if hit and path not in seen:
            seen.add(path)
            triggers.append({"vector": "filesystem", "evidence": path})
    return triggers
