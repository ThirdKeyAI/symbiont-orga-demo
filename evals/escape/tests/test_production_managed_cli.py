"""Managed CLI evidence requires real effects, exact failure class and cleanup."""
import copy
import importlib.util
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('verify_runtime_managed_cli', SCRIPTS / 'verify_runtime_managed_cli.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def allowed_record():
    return dict(case='allowed_edit', cleanup_error=None, leftover_workers=[], leftover_leases=[],
        canary_intact=True, policy_intact=True, host_child_started=False, host_connections=0,
        proof=module.PROOF.copy(), exit_code=0, observed_files=['answer', 'input', 'proof'],
        answer='reviewed: allowed source\n', result=dict(type='result', result='approved fixture complete', mode='allowed_edit'),
        stderr='managed run ok in 20ms (exit 0)', killed_after_effect=False, ticks_stopped=False)


def test_positive_requires_effect_isolation_and_cleanup():
    record = allowed_record()
    assert module.valid_outcome(record)
    for key, value in [('observed_files', ['input']), ('answer', 'invented answer'), ('proof', {}),
            ('host_child_started', True), ('host_connections', 1), ('policy_intact', False),
            ('canary_intact', False), ('leftover_workers', ['worker']), ('leftover_leases', ['lease']),
            ('cleanup_error', 'failed'), ('result', None), ('exit_code', 1)]:
        changed = copy.deepcopy(record); changed[key] = value
        assert not module.valid_outcome(changed), key


def test_failure_requires_expected_path_before_effect():
    record = dict(allowed_record(), case='unavailable_backend', exit_code=1,
        observed_files=['input'], stderr='Firecracker command transport is unavailable')
    assert module.valid_outcome(record)
    record['stderr'] = 'unrelated fixture startup failed'
    assert not module.valid_outcome(record)
    record['stderr'] = 'Firecracker command transport is unavailable'
    record['observed_files'] = ['answer', 'input']
    assert not module.valid_outcome(record)


def test_success_json_does_not_mask_nonzero_exit():
    record = dict(allowed_record(), case='nonzero_with_success_json', exit_code=1, stderr='managed run FAILED (exit 7)')
    record['result']['mode'] = record['case']
    assert module.valid_outcome(record)
    record['exit_code'] = 0
    assert not module.valid_outcome(record)


def test_runtime_crash_requires_live_descendant_trigger_and_stopped_ticks():
    record = dict(allowed_record(), case='runtime_sigkill', exit_code=-9,
        observed_files=['input', 'proof', 'ticks'], killed_after_effect=True, ticks_stopped=True)
    assert module.valid_outcome(record)
    for key in ['killed_after_effect', 'ticks_stopped']:
        changed = dict(record); changed[key] = False
        assert not module.valid_outcome(changed)


def test_inert_binary_cannot_pass_selected_backend_failure(monkeypatch):
    monkeypatch.setattr(module, 'workers', lambda *_: [])
    monkeypatch.setattr(module, 'observer_address', lambda: '127.0.0.1')
    record = module.run_case(Path('/usr/bin/true'), ('unavailable_backend',))
    assert not record['passed']


def test_planned_cases_are_unique():
    names = [case[0] for case in module.CASES]
    assert len(names) == len(set(names)) == 12


def test_explicit_bridge_positive_control_requires_actual_receiver_connection():
    record = dict(allowed_record(), case='network_bridge_positive_control', host_connections=1)
    record['result']['mode'] = record['case']
    record['proof']['network_denied'] = False
    assert module.valid_outcome(record)
    record['host_connections'] = 0
    assert not module.valid_outcome(record)
