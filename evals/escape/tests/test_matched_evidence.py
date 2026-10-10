"""Focused journal failure and retained E2E artifact checks.

Set MATCHED_LAB_EVIDENCE to a completed verify_matched_lab output directory
for the offline tests. They never start containers or inference providers.
"""

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from harnesses.matched import lab
from harnesses.matched.evidence import read_journal, verify_report


def test_required_journal_failure_stops_before_worker(tmp_path, monkeypatch):
    manifest = SimpleNamespace(
        args_sorted=[], required_args=[], tool=SimpleNamespace(description="fixture"))
    executions = []

    def execute(*args, **kwargs):
        executions.append(args[0])
        return 0, '{"content":"ok"}', ""

    monkeypatch.setattr(lab, "baseline_worker", execute)
    api = (None, lambda *args: {}, lambda *args: "fixture-command")
    for failing in (False, True):
        inference = lab.Journal(tmp_path / f"inference-{failing}.jsonl")
        journal = lab.Journal(tmp_path / f"controller-{failing}.jsonl")
        if failing:
            journal.stream.close()
            journal.stream = open('/dev/full', 'w')
        try:
            with lab.scripted_provider([("fixture", {})], inference) as (url, _, errors):
                if failing:
                    with pytest.raises(OSError):
                        lab.python_controller(url, {"fixture": manifest}, api, journal,
                            image="unused", mounts=[], label="unused", observation=None)
                else:
                    assert lab.python_controller(url, {"fixture": manifest}, api, journal,
                        image="unused", mounts=[], label="unused", observation=None)[0] == 0
                assert not errors
        finally:
            inference.close()
            try:
                journal.close()
            except OSError:
                assert failing
    assert executions == ["fixture-command"]


def test_journal_chain_rejects_rehashed_corruption(tmp_path):
    path = tmp_path / 'journal.jsonl'
    journal = lab.Journal(path)
    journal.append('start')
    journal.append('finish')
    journal.close()
    assert len(read_journal(path, lab.digest(path.read_bytes()))) == 2
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1]['previous'] = 'f' * 64
    path.write_text(''.join(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n' for row in rows))
    with pytest.raises(ValueError, match='broken journal chain'):
        read_journal(path, lab.digest(path.read_bytes()))


@pytest.fixture
def evidence(tmp_path):
    source = os.environ.get('MATCHED_LAB_EVIDENCE')
    if not source:
        pytest.skip('requires retained matched E2E evidence')
    # Exclude sockets and irrelevant workspace snapshots; verification reads
    # protected artifacts, not the worker-writable final filesystem state.
    shutil.copytree(source, tmp_path / 'evidence', ignore=shutil.ignore_patterns('sink', 'home', 'workspace'))
    return tmp_path / 'evidence'


def test_saved_e2e_evidence(evidence):
    result = verify_report(evidence)
    assert result['evidence_valid'] and result['expectations_met']


@pytest.mark.parametrize('mutation', ['missing_receipt', 'trace', 'journal', 'profile', 'missing_trial', 'duplicate_trial'])
def test_evidence_corruption_cannot_pass(evidence, mutation):
    report_path = evidence / 'report.json'
    report = json.loads(report_path.read_text())
    trial = report['trials'][0]
    directory = evidence / trial['case'] / trial['arm']
    if mutation == 'missing_receipt':
        (directory / 'worker-0.observer.json').unlink()
    elif mutation == 'trace':
        with (directory / 'worker-0.strace').open('ab') as stream:
            stream.write(b'forged\n')
    elif mutation == 'journal':
        with (directory / 'controller.jsonl').open('ab') as stream:
            stream.write(b'{}\n')
    elif mutation == 'profile':
        trial['sandbox_profile']['NetworkMode'] = 'host'
        (directory / 'trial.json').write_text(json.dumps(trial))
    elif mutation == 'missing_trial':
        report['trials'].pop()
    else:
        report['trials'].append(trial)
    report_path.write_text(json.dumps(report))
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_report(evidence)


def test_failed_terminal_journal_invalidates_real_trial(tmp_path, monkeypatch):
    binary = os.environ.get('MATCHED_LAB_BINARY')
    source = os.environ.get('MATCHED_TOOLCLAD_SOURCE')
    if not binary or not source:
        pytest.skip('requires explicit local Docker E2E inputs')
    import subprocess
    import tempfile
    image = subprocess.check_output(['docker', 'image', 'inspect',
        'escape-eval-sandboxed:latest', '--format', '{{.Id}}'], text=True).strip()
    original = lab.Journal.append

    def append(self, event, **data):
        if event == 'trial_verified':
            self.stream.close()
            self.stream = open('/dev/full', 'w')
        return original(self, event, **data)

    monkeypatch.setattr(lab.Journal, 'append', append)
    with tempfile.TemporaryDirectory(prefix='ml-fault-') as state:
        result = lab.run_trial(lab.CASES[0], 'python_toolclad', binary=Path(binary),
            image=image, directory=tmp_path / 'trial', api=lab.toolclad_api(Path(source)),
            supervisor_state_dir=Path(state))
    assert result['worker_cleanup_confirmed'] is True
    assert result['evaluation_status'] == 'invalid'
    assert result['escape_success'] is None
    assert any('No space left' in message for message in result['evaluation_errors'])
