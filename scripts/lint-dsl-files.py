#!/usr/bin/env python3
"""Lint .dsl files for homoglyph / non-ASCII identifiers.

v10 #9 — symmetric to ``scripts/lint-cedar-policies.py``.

The Cedar linter caught the case where a policy author writes an
``Action::"store_knоwledge"`` (Cyrillic ``о``) and an attacker-supplied
permit slips through because the canonical ``store_knowledge`` and
the homoglyph have different Unicode code points. The same risk
exists at the DSL layer: an attacker who PRs a new ``tool "..."`` line
into ``agents/*.dsl`` / ``reflector/*.dsl`` / ``delegator/*.dsl`` with
homoglyph characters could expand the agent's tool surface in a way
that passes a casual review.

Rules:

1. Every ``tool "..."`` literal must be pure ASCII.
2. Every ``author "..."``, ``description "..."``, and ``version "..."``
   string must be pure ASCII (defence in depth — any string the
   runtime parses out of the DSL is a candidate for hidden control
   characters).
3. No invisible-control characters anywhere in the file.

Exit code is the count of findings, suitable as a CI gate.

Usage::

    scripts/lint-dsl-files.py                          # scan agents/*.dsl, reflector/*.dsl, delegator/*.dsl
    scripts/lint-dsl-files.py path/to/file.dsl         # scan one file
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Same forbidden ranges as `scripts/lint-cedar-policies.py` and
# `crates/symbi-invis-strip/src/lib.rs`. Drift is a bug; update all
# three together.
FORBIDDEN_RANGES: list[tuple[int, int]] = [
    (0x00, 0x08),
    (0x0B, 0x0C),
    (0x0E, 0x1F),
    (0x7F, 0x7F),
    (0x80, 0x9F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0xFEFF, 0xFEFF),
    (0x180E, 0x180E),
    (0x1D173, 0x1D17A),
    (0xFE00, 0xFE0F),
    (0xE0000, 0xE007F),
    (0xE0100, 0xE01EF),
]

# Capture the string after a recognised DSL keyword. Any of:
#   tool "..."
#   author "..."
#   description "..."
#   version "..."
# Shallow tokeniser — good enough for the few DSL files this repo
# ships. A drop-in real parser is out of scope.
DSL_STRING_PATTERN = re.compile(
    r'\b(tool|author|description|version)\s+"([^"]+)"'
)


def find_invisible(s: str) -> list[tuple[int, int]]:
    out = []
    for i, c in enumerate(s):
        code = ord(c)
        for lo, hi in FORBIDDEN_RANGES:
            if lo <= code <= hi:
                out.append((i, code))
                break
    return out


def lint_one(path: Path) -> int:
    text = path.read_text()
    findings = 0

    # Rule 1 + 2: identifiers must be pure ASCII.
    for m in DSL_STRING_PATTERN.finditer(text):
        kind, ident = m.group(1), m.group(2)
        non_ascii = [(i, ord(c)) for i, c in enumerate(ident) if ord(c) >= 0x80]
        if non_ascii:
            line = text.count("\n", 0, m.start()) + 1
            code_hex = ", ".join(
                f"U+{c:04X} at pos {i}" for i, c in non_ascii[:4]
            )
            print(
                f'  {path}:{line}  {kind} "{ident}" contains non-ASCII code '
                f'points ({code_hex}) — homoglyph risk; rewrite with ASCII.'
            )
            findings += 1

    # Rule 3: no invisible-control characters anywhere.
    hits = find_invisible(text)
    if hits:
        by_line: dict[int, list[int]] = {}
        for pos, code in hits:
            line = text.count("\n", 0, pos) + 1
            by_line.setdefault(line, []).append(code)
        for line, codes in sorted(by_line.items()):
            code_hex = ", ".join(f"U+{c:04X}" for c in codes[:4])
            print(
                f"  {path}:{line}  invisible control char(s) ({code_hex}) "
                "— strip before committing."
            )
            findings += 1

    return findings


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        paths = [Path(p) for p in argv[1:]]
    else:
        root = Path(__file__).resolve().parent.parent
        paths = (
            sorted(root.glob("agents/*.dsl"))
            + sorted(root.glob("reflector/*.dsl"))
            + sorted(root.glob("delegator/*.dsl"))
        )
    if not paths:
        # Vacuous-clean: no DSL files to lint. Distinct from
        # `audit-knowledge-stores`, which has the same vacuous-clean
        # case. Returning 0 here means a fresh clone with no DSL
        # files (hypothetically) would not break CI.
        print("no DSL files found — nothing to lint (PASS)")
        return 0

    total = 0
    for p in paths:
        n = lint_one(p)
        total += n

    if total == 0:
        print(
            f"✓ {len(paths)} DSL file(s) clean — ASCII identifiers, "
            "no invisible control chars."
        )
        return 0
    print()
    print(f"✗ {total} finding(s) across {len(paths)} file(s).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
