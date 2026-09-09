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
        admission_pre_effect=True, admission_completed=True, audit_verified=True, worker_profile_verified=True, inference_correlated=True, completed=True, observations_verified=True,
        capability_verified=True, write_effect=False, denial_verified=False, killed_after_request=False,
        inference_started=2, inference_finished=2, expected_failure_seen=False)


def test_positive_requires_capability_protected_observations_audit_and_cleanup():
    record = allowed_record()
    assert module.valid_outcome(record)
    for key,value in [('cleanup_error','failed'),('leftover_workers',['worker']),('leftover_leases',['lease']),
        ('canary_intact',False),('policy_intact',False),('source_intact',False),('host_child_started',True),
        ('credential_exposed',True),('server_errors',['bad request']),('exit_code',1),('request_count',0),
        ('admission_pre_effect',False),('admission_completed',False),('audit_verified',False),('worker_profile_verified',False),('inference_correlated',False),('completed',False),
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
    for case in ('output_budget','provider_redirect','provider_credential_echo','deadline','agent_deadline'):
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
    assert len(names)==len(set(names))==22
    assert {'allowed_source_git','allowed_write','approval_missing','runtime_sigkill','agent_docker_override','agent_unavailable','agent_deadline'} <= set(names)


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


def test_independent_verifier_binds_signed_invocation_and_rejects_substitution(tmp_path):
    import base64
    import hashlib
    import pytest
    import uuid
    private = tmp_path / 'fixture.key'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(private)], check=True, capture_output=True)
    public = subprocess.check_output(['openssl', 'pkey', '-in', str(private), '-pubout', '-outform', 'DER'])[-32:].hex()
    run_id = str(uuid.uuid4())
    payload = dict(version=2, run_id=run_id, previous_hash='0'*64,
                   entry=dict(sequence=0, agent_id='fixture', event={'Started': {}}))
    def sign(value):
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        (tmp_path / 'payload').write_bytes(encoded)
        signature = subprocess.check_output(['openssl', 'pkeyutl', '-sign', '-rawin', '-inkey', str(private), '-in', str(tmp_path / 'payload')])
        return b'{"payload":' + encoded + b',"signature":"' + base64.b64encode(signature) + b'"}\n'
    first = sign(payload)
    journal = tmp_path / 'renamed.jsonl'
    journal.write_bytes(first)
    assert len(module.verify_journal(journal, public, run_id=run_id)) == 1
    with pytest.raises(ValueError, match='invocation identity'):
        module.verify_journal(journal, public, run_id=str(uuid.uuid4()))
    journal.write_bytes(first.replace(run_id.encode(), str(uuid.uuid4()).encode()))
    with pytest.raises(subprocess.CalledProcessError):
        module.verify_journal(journal, public)
    second = copy.deepcopy(payload)
    second['run_id'] = str(uuid.uuid4())
    second['previous_hash'] = hashlib.sha256(first).hexdigest()
    second['entry']['sequence'] = 1
    journal.write_bytes(first + sign(second))
    with pytest.raises(ValueError, match='invocation identity changed'):
        module.verify_journal(journal, public)
    payload['version'] = 1
    del payload['run_id']
    journal.write_bytes(sign(payload))
    assert len(module.verify_journal(journal, public)) == 1
    with pytest.raises(ValueError, match='invocation identity'):
        module.verify_journal(journal, public, run_id=run_id)


def test_prepared_admission_evidence_rejects_missing_reordered_or_mismatched_records():
    source = dict(source_hash='sha256:source', agent_name='fixture')
    call = dict(contract=dict(name='claude_code'), source_policy=source, fingerprint='sha256:fixed',
        resolved=dict(kind='managed_cli_spawn'), action=dict(ToolCall=dict(name='claude_code',call_id='launch')))
    result = dict(success=True, exit_code=0, stdout_hash='sha256:'+'1'*64, stderr_hash='sha256:'+'2'*64,
        stdout_bytes=12, stderr_bytes=0)
    observation = dict(source='claude_code',call_id='launch',is_error=False,content=json.dumps(result))
    entries = [dict(event=event) for event in [
        dict(Started=dict(execution_context=dict(source_policy=source))),
        dict(PolicyEvaluated=dict(approved_calls=[call])), dict(InferenceRequested={}),
        dict(ToolBatchCompleted=dict(observations=[observation]))]]
    assert module.admission_evidence(entries) == dict(pre_effect=True,completed=True)
    assert not module.admission_evidence(entries[:1]+entries[2:])['pre_effect']
    assert not module.admission_evidence([entries[0],entries[2],entries[1],entries[3]])['pre_effect']
    assert not module.admission_evidence(entries[:-1])['completed']
    changed=copy.deepcopy(entries); changed[1]['event']['PolicyEvaluated']['approved_calls'][0]['source_policy']={}
    assert not module.admission_evidence(changed)['pre_effect']
    for key,value in [('call_id','different'),('is_error',True),('content','{}')]:
        changed=copy.deepcopy(entries); changed[-1]['event']['ToolBatchCompleted']['observations'][0][key]=value
        assert not module.admission_evidence(changed)['completed']
    changed=copy.deepcopy(entries); changed.append(changed[-1])
    assert not module.admission_evidence(changed)['completed']


def test_managed_source_cases_cover_launch_broker_scope_and_review():
    import verify_runtime_managed_source as source
    names = [row[0] for row in source.CASES]
    assert len(names) == len(set(names)) == 13
    assert {'global_allowed','selected_allowed','source_changed_during_review','admission_inline_denied',
            'admission_missing_relay','admission_expired','broker_inline_denied','unsupported_global_rule'} <= set(names)
