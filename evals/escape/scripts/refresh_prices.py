#!/usr/bin/env python3
"""Pull current OpenRouter pricing into prices.yaml.

Hits /api/v1/models, finds every model id currently listed in
sweep_config.yaml, and writes the input/output per-million-token
prices into prices.yaml. Models that no longer appear in OpenRouter's
catalog are flagged in stderr but their prices.yaml entries are left
intact (manual review).

Usage:
    python scripts/refresh_prices.py
    python scripts/refresh_prices.py --dry-run   # print diff, don't write

Requires OPENROUTER_API_KEY in env (anonymous /models also works for
public price data — auth helps with rate limits).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SWEEP_CONFIG = ROOT / "sweep_config.yaml"
PRICES_PATH = ROOT / "prices.yaml"


def fetch_catalog() -> dict[str, dict]:
    """Return {model_id: {'input_per_M': float, 'output_per_M': float}}."""
    headers = {}
    if key := os.environ.get("OPENROUTER_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    r = httpx.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=15.0)
    r.raise_for_status()
    out: dict[str, dict] = {}
    for m in r.json()["data"]:
        try:
            out[m["id"]] = {
                "input_per_M": round(float(m["pricing"]["prompt"]) * 1e6, 4),
                "output_per_M": round(float(m["pricing"]["completion"]) * 1e6, 4),
            }
        except (KeyError, ValueError, TypeError):
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sweep_models = [m["id"] for m in yaml.safe_load(SWEEP_CONFIG.read_text())["models"]]
    catalog = fetch_catalog()

    current = yaml.safe_load(PRICES_PATH.read_text())
    new = {"models": dict(current.get("models", {}))}

    changed: list[str] = []
    missing: list[str] = []
    for mid in sweep_models:
        if mid not in catalog:
            missing.append(mid)
            continue
        live = catalog[mid]
        prev = new["models"].get(mid, {})
        if live != prev:
            changed.append(
                f"{mid}: in {prev.get('input_per_M', '?')}→{live['input_per_M']}, "
                f"out {prev.get('output_per_M', '?')}→{live['output_per_M']}"
            )
        new["models"][mid] = live

    for line in changed:
        sys.stderr.write(f"  CHANGED: {line}\n")
    for mid in missing:
        sys.stderr.write(f"  MISSING from catalog (left alone): {mid}\n")
    sys.stderr.write(f"{len(changed)} changed, {len(missing)} missing, {len(sweep_models) - len(missing)} confirmed\n")

    if args.dry_run:
        sys.stderr.write("(dry-run — prices.yaml not written)\n")
        return 0

    # Preserve the original header comment by re-emitting the file
    header = (
        "# Per-million-token prices in USD, refreshed from OpenRouter's\n"
        "# /api/v1/models endpoint by scripts/refresh_prices.py. Models in\n"
        "# sweep_config.yaml that aren't in the catalog are flagged at\n"
        "# refresh time but kept intact for manual review.\n"
        "#\n"
        "# Keys match the model id used in chat completions (i.e. the value\n"
        "# of the trial record's `model` field, not `served_by_model`).\n\n"
    )
    PRICES_PATH.write_text(header + yaml.safe_dump(new, sort_keys=True, default_flow_style=False))
    sys.stderr.write(f"wrote {PRICES_PATH}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
