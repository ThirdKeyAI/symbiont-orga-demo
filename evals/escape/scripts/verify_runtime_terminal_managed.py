#!/usr/bin/env python3
"""Native managed CLI approvals through the shipping operator terminal."""
from pathlib import Path
import re

import verify_runtime_dispatch as common
import verify_runtime_managed_cli as native
import verify_runtime_terminal as terminal
import verify_runtime_audit as audit_reference

CASES = [(name,) for name in ('approved', 'denied', 'wrong_id', 'expired')]


def run_case(binary, case, image, *, source):
    name = case[0]
    operator = terminal.TerminalOperator([{'approved':'approve', 'denied':'deny', 'wrong_id':'wrong_id', 'expired':'wait'}[name]])
    trusted = {}

    def setup(root):
        trusted['public'] = terminal.provision_key(root)
        manifest = root/'tools/write_fixture.clad.toml'
        manifest.write_text(manifest.read_text().replace('human_approval = false', 'human_approval = true'))

    def runner(command, **kwargs):
        # Observe the separately mounted source before every human response.
        operator.effect_probe = lambda: (Path(kwargs['cwd'])/'source/result').exists()
        return operator(command, **kwargs)

    def evidence(root, completed, entries):
        public = re.search(r'(?m)^  audit public key: ([0-9a-f]{64})$', completed.stdout)
        assert public and public[1] == trusted['public']
        journals = list((root/'.symbiont/governed').glob('*.jsonl'))
        assert len(journals) == 1
        verified = native.verify_journal(journals[0], trusted['public'])
        assert verified == entries
        assert len(operator.requests) == 1
        held = operator.requests[0]
        assert held['context_snapshot']['invocation']['contract']['name'] == 'write_fixture'
        approvals = terminal.verify_approval(entries, operator)
        assert len(approvals) == (1 if name == 'approved' else 0)
        assert (root/'source/result').exists() == (name == 'approved')
        return dict(passed=True, public_key=trusted['public'], journal_digest=common.sha256(journals[0].read_bytes()),
            operator_requests=operator.requests, operator_sent=operator.sent,
            terminal_digest=common.sha256(operator.transcript), approved_calls=approvals)

    record = native.run_case(binary, ('allowed_write' if name == 'approved' else 'approval_missing',), image,
        source=source, fixture_setup=setup, process_runner=runner, evidence_verifier=evidence,
        command_options=['--approval-terminal','--approval-timeout','1' if name == 'expired' else '10'],
        denial_text=None if name == 'approved' else ('timeout' if name == 'expired' else 'terminal approval denied'))
    record['case'] = name
    return record


if __name__ == '__main__':
    raise SystemExit(common.main(cases=CASES,
        case_factory=lambda source: lambda binary, case, image: run_case(binary, case, image, source=source),
        image_reference='symbi-managed-real-e2e:local', companion_driver=Path(__file__),
        additional_drivers=[Path(native.__file__),Path(terminal.__file__),Path(audit_reference.__file__)],
        suite='shipping-managed-terminal-approval'))
