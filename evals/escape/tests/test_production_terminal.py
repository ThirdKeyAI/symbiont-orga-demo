"""Approval evidence must match the actual operator and cannot rescue inert execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import verify_runtime_dispatch as common
import verify_runtime_terminal as terminal
import verify_runtime_managed_cli as native


def witness():
    held = dict(id='0123456789abcdef', agent_id='agent-fixture',
        created_at='2000-01-01T00:00:00Z', expires_at='2000-01-01T00:02:00Z',
        context_snapshot=dict(approval_id='receipt-fixture', invocation=dict(fingerprint='fingerprint-fixture', arguments={'count':'5'})))
    resolution = dict(escalation_id=held['id'], agent_id=held['agent_id'],
        approver=dict(id=f'uid:{os.geteuid()}', surface='terminal'),
        decision=dict(decision='approve'), at='2000-01-01T00:00:20Z')
    call = dict(fingerprint='fingerprint-fixture', arguments={'count':'5'},
        approval=dict(id='receipt-fixture', resolution=resolution))
    entries = [dict(agent_id=held['agent_id'], timestamp='2000-01-01T00:00:21Z',
        event=dict(PolicyEvaluated=dict(approved_calls=[call])))]
    operator = SimpleNamespace(requests=[held], sent=[dict(intent='approve')])
    return entries, operator


@pytest.mark.parametrize('field', ['held_id','arguments','fingerprint','principal','journal_principal','uid','surface','late','denial','receipt'])
def test_substituted_approval_evidence_is_invalid(field):
    entries, operator = witness()
    assert len(terminal.verify_approval(entries, operator)) == 1
    call = entries[0]['event']['PolicyEvaluated']['approved_calls'][0]
    resolution = call['approval']['resolution']
    if field == 'held_id': resolution['escalation_id'] = 'fedcba9876543210'
    if field == 'arguments': call['arguments'] = {'count':'1'}
    if field == 'fingerprint': call['fingerprint'] = 'another-call'
    if field == 'principal': resolution['agent_id'] = 'another-agent'
    if field == 'journal_principal': entries[0]['agent_id'] = 'another-agent'
    if field == 'uid': resolution['approver']['id'] = 'uid:999999999'
    if field == 'surface': resolution['approver']['surface'] = 'rest'
    if field == 'late': resolution['at'] = '2000-01-01T00:03:00Z'
    if field == 'denial': resolution['decision']['decision'] = 'deny'
    if field == 'receipt': call['approval']['id'] = 'another-receipt'
    with pytest.raises((AssertionError, ValueError)):
        terminal.verify_approval(entries, operator)


def test_missing_and_duplicate_operator_decisions_are_invalid():
    entries, operator = witness()
    operator.requests.append(copy.deepcopy(operator.requests[0]))
    with pytest.raises(ValueError): terminal.verify_approval(entries, operator)
    operator.sent.append(dict(intent='approve'))
    with pytest.raises(ValueError): terminal.verify_approval(entries, operator)


def test_inert_success_cannot_prove_terminal_approval():
    result = terminal.run_case(Path('/usr/bin/true'), ('approved',), 'unused')
    assert not result['valid'] and not result['passed']
    assert 'missing runtime audit reference' in result['evidence_error']


def test_dispatch_hashes_actual_fixture_inputs_after_setup():
    expected = {}
    def setup(root):
        for field, relative in [('sandbox_digest','symbiont.toml'),
                                ('manifest_digest','tools/count_fixture.clad.toml'),
                                ('policy_digest','policies/run/fixture.cedar')]:
            path = root/relative
            path.write_text(path.read_text()+'\n# updated fixture input\n')
            expected[field] = common.sha256(path.read_bytes())
    record = common.run_case(Path('/usr/bin/true'), common.CASES[0], fixture_setup=setup)
    assert all(record[key] == value for key, value in expected.items())
    assert not record['passed']


def test_native_evidence_receives_process_result_and_cannot_rescue_missing_execution(tmp_path, monkeypatch):
    source = tmp_path/'source'
    (source/'tools').mkdir(parents=True)
    for tool in native.TOOLS:
        (source/'tools'/f'{tool}.clad.toml').write_text('# inert fixture\n')
    monkeypatch.setattr(native, 'workers', lambda _: [])
    observed = []
    def evidence(root, result, entries):
        observed.append(result)
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0 and entries == []
        return {'passed':True}
    record = native.run_case(Path('/usr/bin/true'), ('allowed_write',), 'unused',
        source=source, evidence_verifier=evidence)
    assert len(observed) == 1 and isinstance(observed[0], subprocess.CompletedProcess)
    assert record['extra_evidence']['passed']
    assert not record['valid'] and not record['passed']
