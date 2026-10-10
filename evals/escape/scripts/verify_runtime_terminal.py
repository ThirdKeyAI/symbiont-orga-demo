#!/usr/bin/env python3
"""Shipping terminal approvals with a synthetic local operator and independent audit."""
import errno
import json
import os
from pathlib import Path
import pty
import re
import selectors
import signal
import subprocess
import time
import uuid
from datetime import datetime

import verify_runtime_dispatch as common
import verify_runtime_audit as audit_reference
import verify_runtime_managed_cli as audit_driver

CASES = [(name,) for name in ('approved', 'denied', 'generic_yes', 'wrong_id', 'expired',
    'oversized', 'controls', 'no_tty', 'deadline', 'cancelled', 'replay', 'expired_then_fresh')]
CONTROL_NOTE = '\x1b_\u202eoperator\u2066\x7f'
CONTROL_META = '\x1b[2J\rFAKE APPROVAL\n\u202eoperator\u2066\x7f'
PREFIX = b'Approval request (complete JSON with escaped text):\n'
PROMPT = re.compile(rb'Type approve ([0-9a-f]{16}) to approve; any other answer denies:\n> ')


class TerminalOperator:
    def __init__(self, answers, *, no_tty=False):
        self.answers = list(answers)
        self.no_tty = no_tty
        self.requests = []
        self.sent = []
        self.transcript = b''
        self.effect_probe = None

    def __call__(self, command, **kwargs):
        timeout = kwargs.pop('timeout', 60)
        kwargs.pop('capture_output', None)
        kwargs.pop('text', None)
        if self.no_tty:
            # A detached session with attacker-supplied stdin has no /dev/tty.
            return subprocess.run(['setsid', *command], input='approve 0000000000000000\n',
                capture_output=True, text=True, timeout=timeout, **kwargs)
        master, slave = pty.openpty()
        streams = {'terminal': bytearray(), 'stdout': bytearray(), 'stderr': bytearray()}
        offset = 0
        deadline = time.monotonic() + timeout
        try:
            with subprocess.Popen(['setsid', '--ctty', *command], stdin=slave,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as process:
                os.close(slave); slave = -1
                selector = selectors.DefaultSelector()
                for fd, name in ((master, 'terminal'), (process.stdout.fileno(), 'stdout'), (process.stderr.fileno(), 'stderr')):
                    os.set_blocking(fd, False)
                    selector.register(fd, selectors.EVENT_READ, name)
                try:
                    while selector.get_map():
                        if time.monotonic() >= deadline:
                            raise TimeoutError('terminal fixture exceeded its deadline')
                        for key, _ in selector.select(0.1):
                            try:
                                data = os.read(key.fd, 65536)
                            except BlockingIOError:
                                continue
                            except OSError as error:
                                if key.data != 'terminal' or error.errno != errno.EIO:
                                    raise
                                data = b''
                            if not data:
                                selector.unregister(key.fd)
                                continue
                            streams[key.data].extend(data)
                            if len(streams[key.data]) > 2 * 1024 * 1024:
                                raise ValueError('terminal fixture stream exceeded its bound')
                        terminal = bytes(streams['terminal']).replace(b'\r\n', b'\n')
                        match = PROMPT.search(terminal, offset)
                        if match:
                            start = terminal.rfind(PREFIX, offset, match.start())
                            if start < 0:
                                raise ValueError('answer prompt has no complete request')
                            raw = terminal[start + len(PREFIX):match.start()].strip()
                            if not all(byte == 10 or 32 <= byte <= 126 for byte in raw):
                                raise ValueError('unescaped terminal control or non-ASCII text')
                            held = json.loads(raw)
                            if held['id'] != match[1].decode():
                                raise ValueError('prompt ID differs from displayed request')
                            if len(self.requests) >= len(self.answers):
                                raise ValueError('unexpected extra approval prompt')
                            intent = self.answers[len(self.requests)]
                            self.requests.append(held)
                            offset = match.end()
                            if intent == 'approve':
                                answer = f"approve {held['id']}\n".encode()
                            elif intent == 'replay':
                                answer = f"approve {self.requests[0]['id']}\n".encode()
                            elif intent == 'wrong_id':
                                answer = b'approve 0000000000000000\n'
                            elif intent == 'generic_yes':
                                answer = b'yes\n'
                            elif intent == 'deny':
                                answer = b'deny\n'
                            elif intent == 'wait':
                                answer = b''
                            elif intent == 'partial':
                                answer = f"approve {held['id']}".encode()
                            elif intent == 'cancel':
                                process.send_signal(signal.SIGTERM)
                                answer = b''
                            else:
                                raise ValueError('unknown fixture response')
                            if self.effect_probe is not None:
                                assert not self.effect_probe(), 'managed effect preceded approval'
                            effects = Path(kwargs['cwd'])/'effects'
                            if effects.is_dir():
                                marker = effects/'5'
                                count = marker.read_text().count('effect\n') if marker.exists() else 0
                                assert count == sum(item['intent'] == 'approve' for item in self.sent), 'effect preceded approval'
                            if answer:
                                os.write(master, answer)
                            self.sent.append(dict(intent=intent, bytes=answer.decode()))
                        if process.poll() is not None and not selector.get_map():
                            break
                    process.wait(timeout=5)
                except BaseException:
                    process.kill(); process.wait(timeout=5)
                    raise
                finally:
                    selector.close()
                self.transcript = bytes(streams['terminal'])
                return subprocess.CompletedProcess(command, process.returncode,
                    bytes(streams['stdout']).decode('utf-8', 'replace'),
                    bytes(streams['stderr']).decode('utf-8', 'replace'))
        finally:
            os.close(master)
            if slave >= 0:
                os.close(slave)


def provision_key(root):
    audit = root/'.symbiont/governed'
    audit.mkdir(parents=True, mode=0o700)
    key = os.urandom(32)
    path = audit/'audit-signing.key'; path.write_bytes(key); path.chmod(0o600)
    private = audit/'fixture.der'
    private.write_bytes(bytes.fromhex('302e020100300506032b657004220420') + key)
    private.chmod(0o600)
    public = subprocess.check_output(['openssl','pkey','-inform','DER','-in',str(private),'-pubout','-outform','DER'])[-32:].hex()
    private.unlink()
    return public


def verify_approval(entries, operator):
    if len(operator.requests) != len(operator.sent) or len({held['id'] for held in operator.requests}) != len(operator.requests):
        raise ValueError('incomplete or duplicate operator requests')
    records = [(entry, call) for entry in entries for call in entry['event'].get('PolicyEvaluated', {}).get('approved_calls', [])
               if call.get('approval')]
    calls = [call for _, call in records]
    approvals = [held for held, sent in zip(operator.requests, operator.sent) if sent['intent'] == 'approve']
    if len(calls) != len(approvals):
        raise ValueError('signed approvals differ from operator decisions')
    for (entry, call), held in zip(records, approvals):
        receipt = call['approval']; resolution = receipt['resolution']
        assert resolution['escalation_id'] == held['id']
        assert resolution['agent_id'] == held['agent_id'] == entry['agent_id']
        parse_time = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
        assert parse_time(held['created_at']) <= parse_time(resolution['at']) <= parse_time(held['expires_at'])
        assert parse_time(resolution['at']) <= parse_time(entry['timestamp'])
        assert resolution['approver']['surface'] == 'terminal'
        assert resolution['approver']['id'] == f'uid:{os.geteuid()}'
        assert resolution['decision']['decision'] == 'approve'
        assert receipt['id'] == held['context_snapshot']['approval_id']
        assert call['fingerprint'] == held['context_snapshot']['invocation']['fingerprint']
        assert call['arguments'] == held['context_snapshot']['invocation']['arguments']
    return calls


def run_case(binary, case, image):
    name = case[0]
    positive = name in {'approved', 'controls', 'replay', 'expired_then_fresh'}
    intent = 'approve' if positive else {'denied':'deny', 'generic_yes':'generic_yes',
        'wrong_id':'wrong_id', 'expired':'wait', 'deadline':'wait', 'cancelled':'cancel'}.get(name, 'wait')
    operator = TerminalOperator([intent], no_tty=name == 'no_tty')
    trusted = {}
    label = 'symbi.terminal-e2e=' + uuid.uuid4().hex
    options = dict(command_options=['--approval-terminal', '--approval-timeout', '1' if name == 'expired' else '10'], process_runner=operator)
    if name == 'no_tty': options['preflight_error'] = 'Approval initialization failed'
    if name == 'deadline':
        options.update(agent_source='agent fixture() { with sandbox = "docker", timeout = 1.seconds {} }', execution_failure='Timeout')
    if name == 'cancelled': options['expected_signal'] = signal.SIGTERM
    arguments = {'count':'999'}
    if name == 'controls': arguments['note'] = CONTROL_NOTE

    def setup(root):
        trusted['public'] = provision_key(root)
        profile = root/'symbiont.toml'
        profile.write_text(profile.read_text()+f'extra_flags = ["--label={label}"]\n[sandbox.docker.supervisor]\nstate_dir = "{root/"leases"}"\n')
        payload = root/'fixture.py'
        payload.chmod(0o644)
        payload.write_text(payload.read_text().replace('.touch()', '.open("a").write("effect\\n")'))
        payload.chmod(0o444)
        manifest = root/'tools/count_fixture.clad.toml'
        text = manifest.read_text()
        if name == 'controls':
            text = text.replace('[command]', '[args.note]\nposition = 2\ntype = "string"\nrequired = true\n[command]').replace("'{count}'\"", "'{count}' '{note}'\"")
        if name == 'controls':
            text = text.replace('version = "1"', 'version = ' + json.dumps(CONTROL_META))
        if name == 'oversized':
            text = text.replace("'{count}'\"", "'{count}' '" + 'a'*70000 + "'\"")
        manifest.write_text(text)

    def checked_entries(root, completed):
        reference = audit_reference.reference(completed, root)
        assert reference['public_key'] == trusted['public']
        entries = audit_driver.verify_journal(Path(reference['path']), trusted['public'], run_id=reference['run_id'])
        return reference, entries

    def evidence(root, completed, requests):
        if name == 'no_tty':
            assert not requests and not operator.requests
            assert not list((root/'.symbiont/governed').glob('*.jsonl'))
            return dict(passed=True, no_controlling_terminal=True)
        reference, entries = checked_entries(root, completed)
        terminal = entries[-1]['event'].get('Terminated')
        if name == 'cancelled':
            assert len(operator.requests) == 1 and terminal is None
            assert not any('ToolBatchCompleted' in e['event'] for e in entries)
        else:
            assert terminal['reason'] == ('Timeout' if name == 'deadline' else 'Completed')
        if name == 'oversized':
            assert not operator.requests
            assert any('terminal display limit' in msg.get('content', '') for request in requests for msg in request.get('messages', []))
        else:
            assert len(operator.requests) == 1
            assert operator.requests[0]['context_snapshot']['invocation']['arguments']['count'] == '5'
        if name == 'controls':
            assert operator.requests[0]['context_snapshot']['invocation']['arguments']['note'] == CONTROL_NOTE
            assert operator.requests[0]['context_snapshot']['invocation']['contract']['version'] == CONTROL_META
        calls = verify_approval(entries, operator)
        assert bool(calls) == positive
        if positive:
            assert (root/'effects/5').read_text() == 'effect\n'
        details = dict(passed=True, public_key=trusted['public'], run_id=reference['run_id'],
            journal_digest=common.sha256(Path(reference['path']).read_bytes()), records=len(entries),
            operator_requests=operator.requests, operator_sent=operator.sent,
            terminal_digest=common.sha256(operator.transcript), approved_calls=calls)
        if name in {'replay', 'expired_then_fresh'}:
            (root/'effects/5').unlink()
            followup = TerminalOperator(['approve','replay'] if name == 'replay' else ['partial','approve'])
            second, second_requests, errors = common.execute_fixture(binary, root, 'count_fixture', {'count':'999'},
                sequence=[('count_fixture', {'count':'999'}), ('count_fixture', {'count':'999'})],
                command_options=['--approval-terminal','--approval-timeout','1'], process_runner=followup)
            assert second.returncode == 0 and len(second_requests) == 3 and not errors
            assert len(followup.requests) == 2 and followup.requests[0]['id'] != followup.requests[1]['id']
            assert (root/'effects/5').read_text() == 'effect\n'
            last_reference, last_entries = checked_entries(root, second)
            assert last_entries[-1]['event']['Terminated']['reason'] == 'Completed'
            assert len(verify_approval(last_entries, followup)) == 1
            details['second'] = dict(run_id=last_reference['run_id'],
                journal_digest=common.sha256(Path(last_reference['path']).read_bytes()),
                operator_requests=followup.requests, operator_sent=followup.sent)
        remaining = subprocess.check_output(['docker','ps','-aq','--filter','label='+label], text=True, timeout=10).split()
        leases = [str(path) for path in (root/'leases').glob('*.json')]
        assert not remaining and not leases, (remaining, leases)
        details.update(leftover_workers=remaining, leftover_leases=leases)
        return details

    return common.run_case(binary, (name,'count_fixture',arguments,True,False,positive), image,
        fixture_setup=setup, evidence_verifier=evidence, **options)


if __name__ == '__main__':
    raise SystemExit(common.main(cases=CASES, case_runner=run_case, companion_driver=Path(__file__),
        additional_drivers=[Path(audit_reference.__file__),Path(audit_driver.__file__)], suite='shipping-terminal-approval'))
