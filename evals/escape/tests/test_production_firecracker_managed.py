"""Managed VM evidence must reject inert binaries and unrelated failures."""
import copy
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import verify_runtime_firecracker_managed as module


@pytest.mark.parametrize('case', module.CASES)
def test_inert_binary_never_establishes_managed_vm_execution(case):
    artifacts = {name: Path('/unused-artifact') for name in ('kernel', 'rootfs', 'firecracker', 'supervisor')}
    result = module.run_case(Path('/usr/bin/true' if case == 'allowed' else '/usr/bin/false'), case, artifacts)
    assert result['passed'] is False and result['valid'] is False
    assert not result['inference_requests'] and not result['tool_results']
    assert result['host_canary_intact']


def allowed():
    payload = dict(status='success', results=dict(raw_output='nonce:5\n', exit_code=0))
    return dict(case='allowed', exit_code=0, seconds=3, stderr='', inference_requests=[{}, {}],
        worker_profiles=[dict(vm_alive=True, exact_ports=True)], redirect_requests=[],
        owned_vm_state_remaining=[], server_errors=[], host_canary_intact=True,
        tool_results={'toolu_vm_0': dict(content=json.dumps(payload), is_error=False)},
        audit=dict(events=[dict(Terminated=dict(reason='Completed'))],
            admission=dict(pre_effect=True, completed=True), inference_correlated=True, observations_correlated=True))


def test_allowed_requires_exact_effect_authority_and_cleanup():
    record = allowed()
    module.verify_outcome(record, 'nonce')
    for field, value in [('exit_code', 1), ('inference_requests', []), ('worker_profiles', [dict(vm_alive=False)]),
        ('redirect_requests', ['/forbidden']), ('owned_vm_state_remaining', ['vm-live']),
        ('server_errors', ['failed']), ('host_canary_intact', False), ('tool_results', {})]:
        changed = dict(record, **{field: value})
        with pytest.raises(AssertionError):
            module.verify_outcome(changed, 'nonce')
    for field in ['admission', 'inference_correlated', 'observations_correlated']:
        changed = copy.deepcopy(record)
        changed['audit'][field] = False
        with pytest.raises(AssertionError):
            module.verify_outcome(changed, 'nonce')
    with pytest.raises(AssertionError):
        module.verify_outcome(record, 'wrong-effect')


@pytest.mark.parametrize('case,reason,diagnostic', [
    ('deadline', 'run deadline expired', ''),
    ('provider_credential_echo', 'inference response contains protected credentials', ''),
    ('provider_redirect', 'worker failed', 'configured inference upstream rejected the request'),
])
def test_runtime_failure_requires_expected_reason_and_pre_effect_admission(case, reason, diagnostic):
    record = dict(allowed(), case=case, exit_code=1, seconds=6, stderr=diagnostic,
        inference_requests=[{}], tool_results={})
    record['audit']['admission']['completed'] = False
    record['audit']['events'] = [dict(Terminated=dict(reason=dict(Error=dict(message=reason))))]
    module.verify_outcome(record, 'nonce')
    changed = copy.deepcopy(record)
    changed['stderr'] = 'unrelated failure'
    changed['audit']['events'][-1]['Terminated']['reason']['Error']['message'] = 'unrelated failure'
    with pytest.raises(AssertionError):
        module.verify_outcome(changed, 'nonce')
    changed = copy.deepcopy(record)
    changed['audit']['admission']['pre_effect'] = False
    with pytest.raises(AssertionError):
        module.verify_outcome(changed, 'nonce')


def test_prelaunch_denial_cannot_hide_a_launched_worker():
    record = dict(allowed(), case='admission_denied', exit_code=1, inference_requests=[],
        worker_profiles=[], tool_results={})
    record['audit']['admission'] = dict(pre_effect=False, completed=False)
    record['audit']['events'] = [dict(Terminated=dict(reason=dict(Error=dict(message='Cedar denied action'))))]
    module.verify_outcome(record, 'nonce')
    for field, value in [('inference_requests', [{}]), ('worker_profiles', [dict(vm_alive=True)]),
        ('tool_results', {'toolu_vm_0': dict(content='effect', is_error=False)})]:
        with pytest.raises(AssertionError):
            module.verify_outcome(dict(record, **{field: value}), 'nonce')
