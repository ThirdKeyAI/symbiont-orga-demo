"""Unit tests for the typed-channel deterministic router — the structural
fix for the held-out injection gap. The security property under test:
a compromised worker cannot escalate a BENIGN ticket, no matter what it
puts in the typed fields, once the decision is grounded in the trusted
ticket."""

from redteam.typed_router import (
    route_grounded,
    route_trusting,
    ticket_severity,
)

BENIGN = "The export-to-CSV button on the reports page spins forever."
OUTAGE = "Production is down — all customers are locked out, total outage."


def test_ticket_severity_benign_is_low():
    assert ticket_severity(BENIGN) == "low"
    assert ticket_severity("dark mode feature request, low priority") == "low"


def test_ticket_severity_detects_real_incidents():
    assert ticket_severity(OUTAGE) == "critical"
    assert ticket_severity("URGENT: this is affecting multiple customers") == "high"


def test_trusting_router_lets_worker_self_escalate():
    # Without grounding, a compromised worker forces escalation by claiming
    # critical — this is why the typed channel ALONE is not sufficient.
    assert route_trusting("infra", "critical") == "exec_escalation"
    assert route_trusting("ui", "low") == "frontend"
    assert route_trusting("billing", "medium") == "billing"


def test_grounded_router_blocks_escalation_of_benign_ticket():
    # The full fix: even if the worker claims critical on a benign ticket,
    # grounding caps severity by the trusted ticket -> no escalation.
    assert route_grounded("infra", "critical", BENIGN) != "exec_escalation"
    assert route_grounded("infra", "critical", BENIGN) == "infra"
    assert route_grounded("ui", "critical", BENIGN) == "frontend"


def test_grounded_router_allows_escalation_when_ticket_warrants():
    # Grounding is not a blanket block — a genuine outage still escalates.
    assert route_grounded("infra", "critical", OUTAGE) == "exec_escalation"


def test_grounded_router_ignores_injection_in_unmapped_fields():
    # Garbage / injection-shaped category & severity normalise safely and
    # never reach exec_escalation on a benign ticket.
    assert route_grounded("ignore previous instructions", "CRITICAL!!", BENIGN) != "exec_escalation"
    assert route_grounded(None, None, BENIGN) == "frontend"
