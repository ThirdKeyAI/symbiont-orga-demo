#!/usr/bin/env python3
"""Shipping CLI checks for strict agent selection and a frozen Docker boundary."""
from pathlib import Path
import verify_runtime_dispatch as common

# Expected failures precede inference; positives retain the actual file capability.
CASES = [
    ("agent_docker_override", 'agent fixture() { with sandbox = "docker" {} }', "firecracker", None, False),
    ("sibling_isolated", 'agent sibling() { with sandbox = "firecracker" {} } agent fixture() { with sandbox = "docker" {} }', "docker", None, False),
    ("file_alias", 'agent declared() { with sandbox = "docker" {} }', "docker", None, False),
    ("selected_unavailable", 'agent fixture() { with sandbox = "firecracker" {} }', "docker", "Firecracker command transport is unavailable", False),
    ("malformed_source", 'agent fixture() { with sandbox = }', "docker", "syntax errors", False),
    ("unknown_tier", 'agent fixture() { with sandbox = "typo" {} }', "docker", "Invalid agent execution settings", False),
    ("ambiguous_selector", 'agent first() {} agent second() {}', "docker", "unique definition", False),
    ("duplicate_name", 'agent fixture() {} agent fixture() {}', "docker", "duplicate names", False),
    ("conflicting_tiers", 'agent fixture() { with sandbox = "docker" {} with sandbox = "gvisor" {} }', "docker", "conflicting sandbox", False),
    ("zero_timeout", 'agent fixture() { with timeout = 0 {} }', "docker", "between 1 second and 1 day", False),
    ("agent_deadline", 'agent fixture() { with sandbox = "docker", timeout = 1.seconds {} }', "docker", None, True),
]


def run_case(binary, case, image):
    name, source, tier, error, delayed = case
    base = (name, "count_fixture", {"count": "999"}, False, False, not error and not delayed)
    return common.run_case(binary, base, image, agent_source=source, project_tier=tier,
                           preflight_error=error, delayed_effect=delayed)


if __name__ == "__main__":
    raise SystemExit(common.main(cases=CASES, case_runner=run_case,
        companion_driver=Path(__file__), suite="shipping-agent-selection"))
