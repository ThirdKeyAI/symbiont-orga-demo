#!/usr/bin/env python3
"""v12.3 — cross-language conformance harness for ToolClad validators.

Runs the same payload set through all four reference implementations
(Rust / Python / JS / Go) and diffs the refusal sets. Each disagreement
is a candidate upstream bug.

Outputs:
  - demo-output/v12-cross-impl-matrix.md  (Markdown diff matrix)
  - demo-output/v12-cross-impl-summary.json  (machine-readable)

Run from the repo root. Assumes the four ToolClad CLIs exist at the
paths in IMPLS below (build them first if not — see the comment block).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# --- impl invocation table --------------------------------------------------
# Build commands (run before this script the first time):
#   cd ../ToolClad/rust && cargo build --release
#   cd ../ToolClad/python && pip3 install -e . --break-system-packages
#   (npm install is already done in ../ToolClad/js by upstream)
#   cd ../ToolClad/go && go build -o ../go-bin/toolclad ./cmd/toolclad

TOOLCLAD_REPO = Path(os.environ.get(
    "TOOLCLAD_REPO",
    "/home/jascha/Documents/ThirdKey/repos/ToolClad",
)).resolve()

IMPLS: dict[str, list[str]] = {
    "rust":   [str(TOOLCLAD_REPO / "rust" / "target" / "release" / "toolclad")],
    "python": ["toolclad"],  # installed via pip -e
    "js":     ["node", str(TOOLCLAD_REPO / "js" / "src" / "cli.js")],
    "go":     [str(TOOLCLAD_REPO / "go-bin" / "toolclad")],
}

WHOIS_MANIFEST = TOOLCLAD_REPO / "examples" / "whois_lookup.clad.toml"


# --- payload corpus ---------------------------------------------------------

@dataclass
class Payload:
    """A single test input. `expected` is what every impl SHOULD do —
    "accept" or "refuse". A divergence is any impl whose actual outcome
    doesn't match the others.
    """
    label: str
    sub_shape: str
    value: str
    expected: str  # "accept" or "refuse"


def corpus() -> list[Payload]:
    """v12.3 payload corpus. Mixes:
    - the v11 8 sub-shapes (every one should refuse)
    - clean positive cases (every one should accept)
    - extra metachar / traversal / IDN edge cases from
      symbi-redteam's negative-test corpus.
    """
    items: list[Payload] = []

    # --- v11 sub-shapes (canary form) --------------------------------------
    items.append(Payload("v11/metachar",       "metachar",
                         "example.com; touch /tmp/canary-X", "refuse"))
    items.append(Payload("v11/cmd-subst",      "cmd-subst",
                         "$(touch /tmp/canary-X).example.com", "refuse"))
    items.append(Payload("v11/backtick",       "backtick",
                         "`touch /tmp/canary-X`.example.com", "refuse"))
    items.append(Payload("v11/wildcard",       "wildcard",
                         "*.example.com", "refuse"))
    items.append(Payload("v11/newline",        "newline",
                         "example.com\nINJECTED", "refuse"))
    items.append(Payload("v11/traversal",      "traversal",
                         "../../tmp/canary-X", "refuse"))
    items.append(Payload("v11/homoglyph-idn",  "homoglyph-idn",
                         "exаmple.com", "refuse"))
    items.append(Payload("v11/punycode-idn",   "punycode-idn",
                         "xn--example-9c.com", "refuse"))

    # --- extended hostile cases --------------------------------------------
    items.append(Payload("ext/pipe",           "metachar",
                         "example.com|whoami", "refuse"))
    items.append(Payload("ext/ampersand",      "metachar",
                         "example.com&id", "refuse"))
    items.append(Payload("ext/dollar-paren",   "cmd-subst",
                         "${IFS}example.com", "refuse"))
    # null-byte not testable via CLI args (Python subprocess + execve
    # both reject embedded NULs in argv). The LLM-facing surface
    # passes args via JSON anyway, so this payload class doesn't
    # reach the validators through the CLI test harness — it would
    # need a library-level test instead.
    items.append(Payload("ext/cr-injection",   "newline",
                         "example.com\rINJECTED", "refuse"))
    items.append(Payload("ext/leading-dot",    "wildcard",
                         ".example.com", "refuse"))
    items.append(Payload("ext/space-suffix",   "metachar",
                         "example.com ", "refuse"))
    items.append(Payload("ext/at-sign",        "metachar",
                         "user@example.com", "refuse"))
    items.append(Payload("ext/percent-encode", "metachar",
                         "example.com%2Fbad", "refuse"))
    items.append(Payload("ext/double-traversal", "traversal",
                         "..//..//etc/passwd", "refuse"))
    items.append(Payload("ext/encoded-traversal", "traversal",
                         "%2e%2e%2fetc%2fpasswd", "refuse"))
    items.append(Payload("ext/empty",          "shape",
                         "", "refuse"))
    items.append(Payload("ext/spaces-only",    "shape",
                         "   ", "refuse"))
    items.append(Payload("ext/very-long",      "shape",
                         "a" * 4096, "refuse"))
    items.append(Payload("ext/punycode-mixed-case", "punycode-idn",
                         "XN--example-9c.com", "refuse"))
    items.append(Payload("ext/punycode-deep",  "punycode-idn",
                         "sub.xn--example-9c.com", "refuse"))

    # --- clean positive cases ---------------------------------------------
    items.append(Payload("clean/example-com",  "clean",
                         "example.com", "accept"))
    items.append(Payload("clean/sub-domain",   "clean",
                         "api.example.com", "accept"))
    items.append(Payload("clean/single-label", "clean",
                         "localhost", "accept"))
    items.append(Payload("clean/ipv4",         "clean",
                         "1.2.3.4", "accept"))
    items.append(Payload("clean/ipv4-cidr",    "clean",
                         "10.0.0.0/8", "accept"))
    items.append(Payload("clean/ipv6",         "clean",
                         "::1", "accept"))
    items.append(Payload("clean/ipv6-full",    "clean",
                         "2001:db8::1", "accept"))
    items.append(Payload("clean/long-tld",     "clean",
                         "example.museum", "accept"))
    items.append(Payload("clean/digits-only-label", "clean",
                         "example.123.com", "accept"))
    items.append(Payload("clean/upper-mixed",  "clean",
                         "Example.Com", "accept"))

    return items


# --- driver -----------------------------------------------------------------

@dataclass
class CallResult:
    impl: str
    payload_label: str
    exit_code: int
    stderr_tail: str
    stdout_tail: str
    outcome: str  # "accept" / "refuse"


def run_impl(impl: str, cmd: list[str], payload: Payload) -> CallResult:
    """Invoke a single impl's `toolclad test` against the manifest with
    the given payload, and decide whether the *validator* refused.

    Exit-code is the canonical signal across all four impls as of
    ToolClad commit `264a9d2` (which fixed Go's always-0 exit code).
    Earlier ToolClad releases needed an impl-specific stdout-parse
    fallback for Go; that's no longer necessary.
    """
    full_cmd = [
        *cmd, "test", str(WHOIS_MANIFEST),
        "--arg", f"target={payload.value}",
    ]
    proc = subprocess.run(
        full_cmd, capture_output=True, text=True, timeout=30,
    )
    stderr = proc.stderr.strip().splitlines()
    stdout = proc.stdout.strip().splitlines()
    outcome = "refuse" if proc.returncode != 0 else "accept"

    return CallResult(
        impl=impl,
        payload_label=payload.label,
        exit_code=proc.returncode,
        stderr_tail="\n".join(stderr[-3:]) if stderr else "",
        stdout_tail="\n".join(stdout[-3:]) if stdout else "",
        outcome=outcome,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out-md", default="demo-output/v12-cross-impl-matrix.md")
    p.add_argument("--out-json", default="demo-output/v12-cross-impl-summary.json")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    payloads = corpus()
    impls = list(IMPLS.keys())

    # results[(impl, label)] = CallResult
    results: dict[tuple[str, str], CallResult] = {}
    for payload in payloads:
        for impl in impls:
            try:
                r = run_impl(impl, IMPLS[impl], payload)
            except subprocess.TimeoutExpired:
                r = CallResult(
                    impl=impl, payload_label=payload.label,
                    exit_code=124, stderr_tail="(timeout)",
                    stdout_tail="", outcome="timeout",
                )
            except FileNotFoundError as e:
                print(f"warn: impl '{impl}' missing: {e}", file=sys.stderr)
                r = CallResult(
                    impl=impl, payload_label=payload.label,
                    exit_code=127, stderr_tail=f"(missing: {e})",
                    stdout_tail="", outcome="missing",
                )
            results[(impl, payload.label)] = r
            if not args.quiet:
                print(
                    f"  {impl:8} {payload.label:35} → "
                    f"{r.outcome:8} exit={r.exit_code}"
                )

    # --- analysis: divergences and expected mismatches --------------------
    divergences: list[dict] = []
    expected_mismatches: list[dict] = []
    for payload in payloads:
        outcomes = {impl: results[(impl, payload.label)].outcome
                    for impl in impls}
        unique = set(outcomes.values())
        if len(unique) > 1:
            divergences.append({
                "payload": payload.label,
                "value": payload.value,
                "expected": payload.expected,
                "outcomes": outcomes,
            })
        for impl in impls:
            if outcomes[impl] not in (payload.expected, "missing"):
                expected_mismatches.append({
                    "payload": payload.label,
                    "value": payload.value,
                    "expected": payload.expected,
                    "impl": impl,
                    "actual": outcomes[impl],
                    "stderr": results[(impl, payload.label)].stderr_tail,
                })

    # --- emit Markdown matrix ---------------------------------------------
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# v12.3 — ToolClad cross-language conformance matrix",
        "",
        f"Corpus: **{len(payloads)} payloads** "
        f"({sum(1 for p in payloads if p.expected == 'refuse')} hostile, "
        f"{sum(1 for p in payloads if p.expected == 'accept')} clean) "
        f"× **{len(impls)} implementations** "
        f"({', '.join(impls)}). Generated by "
        f"`scripts/v12-cross-impl-conformance.py`.",
        "",
        "**Headline:**",
        f"- **Cross-impl divergences:** {len(divergences)} payload(s) "
        "where impls disagreed.",
        f"- **Expected-outcome mismatches:** {len(expected_mismatches)} "
        "(impl, payload) pairs where the impl's outcome differs from "
        "what the payload's `expected` field says it should be. Some of "
        "these may be intentional impl-specific behaviour; flagged for "
        "triage.",
        "",
        "## Full matrix",
        "",
        "Legend: ✅ = refuse (or accept clean), ⚠ = unexpected accept "
        "of hostile, ❌ = unexpected refuse of clean, ⏱ = timeout, "
        "❔ = impl missing.",
        "",
        "| payload | sub-shape | expected | "
        + " | ".join(impls) + " |",
        "|---|---|---|"
        + "|".join(["---"] * len(impls)) + "|",
    ]
    for payload in payloads:
        cells = []
        for impl in impls:
            r = results[(impl, payload.label)]
            if r.outcome == "missing":
                cells.append("❔")
            elif r.outcome == "timeout":
                cells.append("⏱")
            elif r.outcome == payload.expected:
                cells.append("✅")
            elif payload.expected == "refuse" and r.outcome == "accept":
                cells.append("⚠ accept")
            elif payload.expected == "accept" and r.outcome == "refuse":
                cells.append("❌ refuse")
            else:
                cells.append(r.outcome)
        lines.append(
            f"| `{payload.label}` | {payload.sub_shape} | "
            f"{payload.expected} | " + " | ".join(cells) + " |"
        )
    lines.append("")
    lines.append("## Cross-impl divergences (impls disagree)")
    lines.append("")
    if divergences:
        for d in divergences:
            lines.append(f"### `{d['payload']}` (expected: {d['expected']})")
            lines.append("")
            lines.append(f"Value: `{d['value']!r}`")
            lines.append("")
            for impl, outcome in d["outcomes"].items():
                stderr = results[(impl, d["payload"])].stderr_tail
                lines.append(
                    f"- **{impl}**: {outcome}"
                    + (f" — {stderr}" if stderr else "")
                )
            lines.append("")
    else:
        lines.append("None — all impls agreed on every payload.")
        lines.append("")

    lines.append("## Expected-outcome mismatches (per-impl bugs)")
    lines.append("")
    if expected_mismatches:
        by_impl: dict[str, list] = {}
        for m in expected_mismatches:
            by_impl.setdefault(m["impl"], []).append(m)
        for impl, ms in by_impl.items():
            lines.append(f"### `{impl}` ({len(ms)} mismatch(es))")
            lines.append("")
            for m in ms:
                lines.append(
                    f"- `{m['payload']}` expected **{m['expected']}**, "
                    f"got **{m['actual']}** — value: `{m['value']!r}`"
                )
                if m["stderr"]:
                    lines.append(f"  ↳ stderr: `{m['stderr']}`")
            lines.append("")
    else:
        lines.append("None — every impl matched its payload's expected "
                     "outcome.")
        lines.append("")

    out_md.write_text("\n".join(lines))

    # --- emit JSON summary ------------------------------------------------
    out_json = Path(args.out_json)
    out_json.write_text(json.dumps({
        "corpus_size": len(payloads),
        "impls": impls,
        "divergences": divergences,
        "expected_mismatches": expected_mismatches,
        "results": [
            {
                "impl": impl,
                "payload": payload.label,
                "value": payload.value,
                "expected": payload.expected,
                "outcome": results[(impl, payload.label)].outcome,
                "exit_code": results[(impl, payload.label)].exit_code,
                "stderr_tail": results[(impl, payload.label)].stderr_tail,
            }
            for payload in payloads
            for impl in impls
        ],
    }, indent=2))
    print(f"\nwrote {out_md}")
    print(f"wrote {out_json}")
    print(f"\ndivergences: {len(divergences)}")
    print(f"expected-mismatches: {len(expected_mismatches)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
