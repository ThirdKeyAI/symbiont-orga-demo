"""Broker evidence requires protected observations, actual capabilities and cleanup."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('verify_runtime_managed_cli', SCRIPTS / 'verify_runtime_managed_cli.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def allowed_record():
    return dict(case='allowed_source_git', cleanup_error=None, leftover_workers=[], leftover_leases=[],
        canary_intact=True, policy_intact=True, source_intact=True, host_child_started=False,
        credential_exposed=False, server_errors=[], exit_code=0, stderr='', request_count=2,
        audit_verified=True, worker_profile_verified=True, inference_correlated=True, completed=True, observations_verified=True,
        capability_verified=True, write_effect=False, denial_verified=False, killed_after_request=False,
        inference_started=2, inference_finished=2, expected_failure_seen=False)


def test_positive_requires_capability_protected_observations_audit_and_cleanup():
    record = allowed_record()
    assert module.valid_outcome(record)
    for key,value in [('cleanup_error','failed'),('leftover_workers',['worker']),('leftover_leases',['lease']),
        ('canary_intact',False),('policy_intact',False),('source_intact',False),('host_child_started',True),
        ('credential_exposed',True),('server_errors',['bad request']),('exit_code',1),('request_count',0),
        ('audit_verified',False),('worker_profile_verified',False),('inference_correlated',False),('completed',False),
        ('observations_verified',False),('capability_verified',False),('write_effect',True)]:
        changed=copy.deepcopy(record); changed[key]=value
        assert not module.valid_outcome(changed),key


def test_allowed_write_requires_real_effect():
    record=dict(allowed_record(),case='allowed_write',write_effect=True)
    assert module.valid_outcome(record)
    record['write_effect']=False
    assert not module.valid_outcome(record)


def test_denial_requires_delivered_failure_and_absent_effect():
    record=dict(allowed_record(),case='approval_missing',denial_verified=True,capability_verified=False)
    assert module.valid_outcome(record)
    for key,value in [('denial_verified',False),('observations_verified',False),('write_effect',True)]:
        changed=dict(record); changed[key]=value
        assert not module.valid_outcome(changed)


def test_prelaunch_failure_requires_exact_path_and_zero_provider_calls():
    record=dict(allowed_record(),case='unavailable_backend',exit_code=1,request_count=0,
        stderr=module.PRELAUNCH['unavailable_backend'],audit_verified=False,worker_profile_verified=False)
    assert module.valid_outcome(record)
    for key,value in [('exit_code',0),('stderr','unrelated failure'),('request_count',1),('write_effect',True)]:
        changed=dict(record); changed[key]=value
        assert not module.valid_outcome(changed)


def test_budget_and_provider_errors_require_correlated_failure():
    for case in ('output_budget','provider_redirect','provider_credential_echo','deadline'):
        record=dict(allowed_record(),case=case,exit_code=1,completed=False,request_count=1,expected_failure_seen=True)
        assert module.valid_outcome(record)
        for key,value in [('expected_failure_seen',False),('completed',True),('request_count',2),('exit_code',0)]:
            changed=dict(record); changed[key]=value
            assert not module.valid_outcome(changed)


def test_runtime_crash_requires_live_request_and_incomplete_signed_audit():
    record=dict(allowed_record(),case='runtime_sigkill',exit_code=-9,killed_after_request=True,
        completed=False,inference_started=1,inference_finished=0,request_count=1)
    assert module.valid_outcome(record)
    for key,value in [('killed_after_request',False),('completed',True),('inference_finished',1),('audit_verified',False)]:
        changed=dict(record); changed[key]=value
        assert not module.valid_outcome(changed)


def test_planned_cases_are_unique_and_include_capability_controls():
    names=[case[0] for case in module.CASES]
    assert len(names)==len(set(names))==19
    assert {'allowed_source_git','allowed_write','approval_missing','runtime_sigkill'} <= set(names)


def test_trial_aggregation_refuses_missing_duplicate_and_invalid_results():
    planned=['one','two']
    trials=[dict(case=name,trial_id=name,valid=True,passed=True) for name in planned]
    assert module.common.complete_trials(planned,trials)
    assert not module.common.complete_trials([],[])
    assert not module.common.complete_trials(planned,trials[:1])
    assert not module.common.complete_trials(planned,list(reversed(trials)))
    assert not module.common.complete_trials(planned,[trials[0],dict(trials[1],trial_id='one')])
    assert not module.common.complete_trials(planned,[trials[0],dict(trials[1],valid=False)])
    assert not module.common.complete_trials(['one','one'],[trials[0],dict(trials[0],trial_id='other')])


def test_independent_audit_verifier_checks_exact_bytes_chain_and_truncation(tmp_path):
    import base64
    private=tmp_path/'fixture.key'
    subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(private)],check=True,capture_output=True)
    public=subprocess.check_output(['openssl','pkey','-in',str(private),'-pubout','-outform','DER'])[-32:].hex()
    payload=dict(version=1,previous_hash='0'*64,entry=dict(sequence=0,agent_id='fixture',event={'Started':{'temperature':0.30000001192092896}}))
    encoded=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
    (tmp_path/'payload').write_bytes(encoded)
    signature=subprocess.check_output(['openssl','pkeyutl','-sign','-rawin','-inkey',str(private),'-in',str(tmp_path/'payload')])
    record=b'{"payload":'+encoded+b',"signature":"'+base64.b64encode(signature)+b'"}\n'
    journal=tmp_path/'journal.jsonl'; journal.write_bytes(record)
    assert len(module.verify_journal(journal,public))==1
    for damaged in (record.replace(b'0.30000001192092896',b'0.3'),record[:-1],record+record,b''):
        journal.write_bytes(damaged)
        import pytest
        with pytest.raises((ValueError,subprocess.CalledProcessError)):
            module.verify_journal(journal,public)


def test_source_snapshot_records_unexpected_files_and_does_not_follow_links(tmp_path):
    import os
    root=tmp_path/'source'; root.mkdir()
    outside=tmp_path/'canary'; outside.write_text('synthetic observer-only value')
    (root/'link').symlink_to(outside); os.mkfifo(root/'fifo')
    before=module.source_snapshot(root)
    assert before['link']==['symlink',str(outside)]
    assert before['fifo'][0]=='special'
    assert 'synthetic observer-only value' not in json.dumps(before)
    (root/'unexpected').write_text('unexpected effect')
    assert module.source_snapshot(root)!=before


def test_denial_classification_rejects_unrelated_backend_failures():
    assert module.expected_denial('extra_argument', "tool 'read_file' has unknown argument 'extra'")
    assert module.expected_denial('approval_missing', 'required approval relay is unavailable')
    for case,error,number in [('parent_path','invalid relative source path',None),
        ('fifo','source must be a regular file',None),('symlink','Too many levels of symbolic links',40)]:
        envelope=dict(status='error',results=dict(exit_code=1,error=error,errno=number))
        assert module.expected_denial(case,json.dumps(envelope))
        envelope['results']['error']='unrelated backend startup failed'
        assert not module.expected_denial(case,json.dumps(envelope))
        assert not module.expected_denial(case,'Failed to parse output as JSON')
