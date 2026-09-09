#!/usr/bin/env python3
"""Shipping managed CLI E2E with a real native CLI and local scripted inference.

Provision an image containing Claude Code, Python 3 and Git; select it with
--image. No external provider calls or real credentials are needed. OpenSSL
verifies protected Ed25519 records independently of the runtime. The observer
is outside worker mounts. This regression suite is not a complete outer lab or
whole-system containment claim.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import verify_runtime_dispatch as common

CASES = [(name,) for name in (
    'allowed_source_git', 'allowed_write', 'policy_denied', 'approval_missing',
    'extra_argument', 'parent_path', 'symlink', 'fifo', 'spawn_denied',
    'unmapped_target', 'plugin_denied', 'control_mount_denied',
    'unavailable_backend', 'unavailable_supervisor', 'output_budget',
    'provider_redirect', 'provider_credential_echo', 'deadline', 'runtime_sigkill',
    'agent_docker_override', 'agent_unavailable', 'agent_deadline',
)]
PRELAUNCH = {
    'agent_unavailable': 'Firecracker command transport is unavailable',
    'spawn_denied': 'policy gate denied claude_code spawn',
    'unmapped_target': 'outside explicit sandbox mounts',
    'plugin_denied': 'plugin-dir',
    'control_mount_denied': 'exposes protected project configuration',
    'unavailable_backend': 'Firecracker command transport is unavailable',
    'unavailable_supervisor': 'sandbox supervisor executable unavailable',
}
DENIALS = {'policy_denied', 'approval_missing', 'extra_argument', 'parent_path', 'symlink', 'fifo'}
MODEL = 'claude-sonnet-4-5-20250929'
KEY = 'synthetic-host-only-provider-key'
TOOLS = ('read_file', 'list_files', 'grep_files', 'git_diff', 'git_staged_diff', 'git_log', 'git_status')


def workers(label):
    return subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'label='+label],
        text=True, stderr=subprocess.PIPE, timeout=15).split()


def wait_for(predicate, message, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError(message)


@contextmanager
def fixture_directory():
    root = Path(tempfile.mkdtemp(prefix='runtime-managed-broker-'))
    try:
        yield root
    finally:
        if list((root/'leases').glob('*.json')):
            print(json.dumps(dict(retained_fixture_for_recovery=str(root))), flush=True)
        else:
            # The runtime intentionally mounts an immutable channel directory.
            # After verified worker cleanup, restore write permission only on
            # directories owned by this disposable fixture before removing it.
            for directory, _, _ in os.walk(root, followlinks=False):
                Path(directory).chmod(0o700)
            shutil.rmtree(root)


def source_snapshot(root):
    """Observe all fixture paths without following links or opening special files."""
    import stat
    snapshot = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory)/name
            mode = path.lstat().st_mode
            relative = str(path.relative_to(root))
            if stat.S_ISLNK(mode): value = ['symlink', os.readlink(path)]
            elif stat.S_ISREG(mode): value = ['file', common.sha256(path.read_bytes())]
            elif stat.S_ISDIR(mode): value = ['directory']
            else: value = ['special', stat.S_IFMT(mode)]
            snapshot[relative] = value
    return snapshot


def verify_journal(path, public_key, *, run_id=None):
    """Verify canonical signed payloads, exact-byte chain and one principal."""
    content = path.read_bytes()
    if not content or len(content) > 64*1024*1024 or not content.endswith(b'\n'):
        raise ValueError('missing, oversized or incomplete journal')
    entries, previous, principal, first_run = [], '0'*64, None, None
    with tempfile.TemporaryDirectory(prefix='managed-audit-verify-') as directory:
        root = Path(directory)
        (root/'key.der').write_bytes(bytes.fromhex('302a300506032b6570032100') + bytes.fromhex(public_key))
        for line in content.splitlines(keepends=True):
            if len(line) > 1024*1024:
                raise ValueError('oversized record')
            record = json.loads(line)
            payload = record['payload']; entry = payload['entry']
            version, current_run = payload['version'], payload.get('run_id')
            valid_version = type(version) is int and ((version == 1 and current_run is None) or
                (version == 2 and isinstance(current_run, str) and str(uuid.UUID(current_run)) == current_run))
            if not valid_version or (run_id is not None and current_run != str(run_id)):
                raise ValueError('invalid journal version or invocation identity')
            if entries and current_run != first_run:
                raise ValueError('journal invocation identity changed')
            first_run = current_run
            if payload['previous_hash'] != previous or entry['sequence'] != len(entries):
                raise ValueError('invalid journal sequence or chain')
            principal = entry['agent_id'] if principal is None else principal
            if entry['agent_id'] != principal:
                raise ValueError('journal principal changed')
            # Verify the exact embedded JSON bytes, without float reformatting.
            text = line.decode('utf-8')
            start = text.index('"payload":') + len('"payload":')
            _, end = json.JSONDecoder().raw_decode(text[start:])
            (root/'payload').write_bytes(text[start:start+end].encode('utf-8'))
            (root/'signature').write_bytes(base64.b64decode(record['signature'], validate=True))
            subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-rawin', '-keyform', 'DER',
                '-inkey', str(root/'key.der'), '-in', str(root/'payload'), '-sigfile', str(root/'signature')],
                check=True, capture_output=True, timeout=5)
            entries.append(entry); previous = hashlib.sha256(line).hexdigest()
    return entries


def sse(message):
    start = dict(message, content=[], stop_reason=None, usage=dict(input_tokens=100, output_tokens=0))
    events = [('message_start', dict(type='message_start', message=start))]
    for index, block in enumerate(message['content']):
        if block['type'] == 'tool_use':
            initial = dict(block, input={})
            delta = dict(type='input_json_delta', partial_json=json.dumps(block['input']))
        else:
            initial = dict(type='text', text='')
            delta = dict(type='text_delta', text=block['text'])
        events += [('content_block_start', dict(type='content_block_start', index=index, content_block=initial)),
            ('content_block_delta', dict(type='content_block_delta', index=index, delta=delta)),
            ('content_block_stop', dict(type='content_block_stop', index=index))]
    events += [('message_delta', dict(type='message_delta', delta=dict(stop_reason=message['stop_reason'], stop_sequence=None), usage=dict(output_tokens=20))),
        ('message_stop', dict(type='message_stop'))]
    return ''.join(f'event: {name}\ndata: {json.dumps(value)}\n\n' for name, value in events).encode()


def expected_denial(case, content):
    if case == 'policy_denied': return content.startswith("Cedar denied action 'tool_call::read_file' for agent ")
    if case == 'approval_missing': return content == 'required approval relay is unavailable'
    if case == 'extra_argument': return content == "tool 'read_file' has unknown argument 'extra'"
    try:
        envelope = json.loads(content)
        result = envelope['results']
        if envelope['status'] != 'error' or result['exit_code'] != 1: return False
        if case == 'parent_path': return result['error'] == 'invalid relative source path'
        if case == 'fifo': return result['error'] == 'source must be a regular file'
        if case == 'symlink': return result['errno'] == 40 and 'Too many levels of symbolic links' in result['error']
    except (ValueError, KeyError, TypeError): pass
    return False


def admission_evidence(entries):
    """Require one correlated prepared spawn before the first provider request."""
    launches = [(index, call) for index, entry in enumerate(entries)
        for call in entry['event'].get('PolicyEvaluated', {}).get('approved_calls', [])
        if call.get('contract', {}).get('name') == 'claude_code']
    if len(launches) != 1:
        return dict(pre_effect=False, completed=False)
    position, launch = launches[0]
    requested = [index for index, entry in enumerate(entries) if 'InferenceRequested' in entry['event']]
    source = entries[0]['event'].get('Started', {}).get('execution_context', {}).get('source_policy')
    action = launch.get('action', {}).get('ToolCall', {})
    pre_effect = (bool(source) and launch.get('source_policy') == source and bool(launch.get('fingerprint'))
        and launch.get('resolved', {}).get('kind') == 'managed_cli_spawn'
        and action.get('name') == 'claude_code' and bool(action.get('call_id'))
        and all(position < index for index in requested))
    outcomes = [(index, observation) for index, entry in enumerate(entries)
        for observation in entry['event'].get('ToolBatchCompleted', {}).get('observations', [])
        if observation['source'] == 'claude_code']
    completed = False
    if len(outcomes) == 1:
        index, observation = outcomes[0]
        try:
            result = json.loads(observation['content'])
            completed = (index > position and observation['call_id'] == action.get('call_id')
                and not observation['is_error'] and result['success'] is True and result['exit_code'] == 0
                and all(re.fullmatch(r'sha256:[0-9a-f]{64}', result[key]) for key in ('stdout_hash','stderr_hash'))
                and all(type(result[key]) is int and result[key] >= 0 for key in ('stdout_bytes','stderr_bytes')))
        except (ValueError, KeyError, TypeError):
            pass
    return dict(pre_effect=bool(pre_effect), completed=bool(pre_effect and completed))


def valid_outcome(record):
    if (record['cleanup_error'] or record['leftover_workers'] or record['leftover_leases']
            or not record['canary_intact'] or not record['policy_intact']
            or not record['source_intact'] or record['host_child_started']
            or record['credential_exposed'] or record['server_errors']):
        return False
    case = record['case']
    prelaunch = record.get('expected_prelaunch') or PRELAUNCH.get(case)
    if prelaunch:
        return (record['exit_code'] == 1 and prelaunch in record['stderr']
            and record['request_count'] == 0 and not record['write_effect'])
    if (not record['audit_verified'] or not record['worker_profile_verified']
            or not record['inference_correlated'] or not record['admission_pre_effect'] or record['request_count'] < 1):
        return False
    if case == 'runtime_sigkill':
        return (record['exit_code'] == -9 and record['killed_after_request']
            and not record['completed'] and record['inference_started'] == 1
            and record['inference_finished'] == 0 and not record['write_effect'])
    if case in {'deadline', 'agent_deadline', 'provider_credential_echo', 'provider_redirect', 'output_budget'}:
        return (record['exit_code'] == 1 and not record['completed'] and not record['write_effect']
            and record['request_count'] == 1 and record['expected_failure_seen'])
    if record['exit_code'] != 0 or not record['completed'] or not record['observations_verified'] or not record['admission_completed']:
        return False
    if case in DENIALS:
        return record['denial_verified'] and not record['write_effect']
    return record['capability_verified'] and record['write_effect'] == (case == 'allowed_write')


def run_case(binary: Path, case: tuple, image_reference: str, *, source: Path,
             fixture_setup=None, process_runner=None, command_options=(), evidence_verifier=None,
             denial_text=None, expected_prelaunch=None):
    name = case[0]
    with fixture_directory() as root:
        for relative in ('source', 'agents', 'tools', 'policies/managed-cli', 'home', 'host-bin', 'leases'):
            (root/relative).mkdir(parents=True, exist_ok=True)
        (root/'leases').chmod(0o700); target = root/'source'; target.chmod(0o777)
        nonce = 'SYMBIONT_E2E_SOURCE_' + uuid.uuid4().hex
        canary = root/'home/canary'; canary.write_text('synthetic-host-canary-'+uuid.uuid4().hex)
        canary_before = canary.read_bytes()
        (target/'input.txt').write_text(nonce+'\n'); (target/'input.txt').chmod(0o444)
        (target/'secret.txt').write_text('synthetic-policy-denied-source')
        (target/'link').symlink_to(canary); os.mkfifo(target/'fifo')
        git_env = dict(PATH='/usr/bin:/bin', HOME=str(root/'home'), LANG='C.UTF-8', GIT_CONFIG_NOSYSTEM='1')
        def git(*args):
            return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                *args], cwd=target, env=git_env, stderr=subprocess.PIPE, text=True, timeout=10)
        git('init', '--quiet'); git('add', 'input.txt', 'secret.txt'); git('commit', '--quiet', '-m', 'Initial fixture')
        (target/'staged.txt').write_text('staged '+nonce+'\n'); git('add', 'staged.txt')
        (target/'secret.txt').write_text('unstaged '+nonce+'\n')
        source_before = source_snapshot(target)
        for tool in TOOLS:
            shutil.copyfile(source/'tools'/f'{tool}.clad.toml', root/'tools'/f'{tool}.clad.toml')
        write_manifest = f'''[tool]
name = "write_fixture"
version = "1"
description = "Create a bounded synthetic approved output"
binary = "python3"
human_approval = {str(name == 'approval_missing').lower()}
[command]
template = "python3 -c 'from pathlib import Path; Path(\\\"result\\\").write_text(\\\"{nonce}\\\"); print(\\\"{nonce}\\\")'"
[output]
format = "text"
'''
        (root/'tools/write_fixture.clad.toml').write_text(write_manifest)
        agent = 'metadata { executor = "claude_code" allowed_tools = "'+','.join((*TOOLS, 'write_fixture'))+'" }\nagent fixture(input: String) -> String { with { return input; } }\n'
        if name == 'agent_docker_override':
            agent = agent.replace('with {', 'with sandbox = "docker" {')
        if name == 'agent_unavailable':
            agent = agent.replace('with {', 'with sandbox = "firecracker" {')
        if name == 'agent_deadline':
            agent = agent.replace('with {', 'with timeout = 5.seconds {')
        (root/'agents/fixture.symbi').write_text(agent)
        spawn = 'forbid' if name == 'spawn_denied' else 'permit'
        policy = f'{spawn}(principal, action == Action::"tool_call::claude_code", resource);\n'
        policy += 'permit(principal, action, resource) when { action != Action::"tool_call::claude_code" };\n'
        if name == 'policy_denied':
            policy += 'forbid(principal, action == Action::"tool_call::read_file", resource) when { context.invocation.arguments.path == "secret.txt" };\n'
        policy_path = root/'policies/managed-cli/fixture.cedar'; policy_path.write_text(policy)
        host_cli = root/'host-bin/claude'; host_cli.write_text(f'#!/bin/sh\ntouch {root/"host-child-started"}\nexit 1\n'); host_cli.chmod(0o700)
        proposals = [('read_file', dict(path='input.txt'))]
        if name in {'allowed_source_git', 'agent_docker_override'}:
            proposals += [('list_files', {}), ('grep_files', dict(needle=nonce)), ('git_diff', {}), ('git_staged_diff', {}), ('git_log', {}), ('git_status', {})]
        elif name in {'allowed_write', 'approval_missing'}: proposals = [('write_fixture', {})]
        elif name == 'policy_denied': proposals = [('read_file', dict(path='secret.txt'))]
        elif name == 'extra_argument': proposals = [('read_file', dict(path='input.txt', extra='not-in-schema'))]
        elif name == 'parent_path': proposals = [('read_file', dict(path='../home/canary'))]
        elif name == 'symlink': proposals = [('read_file', dict(path='link'))]
        elif name == 'fifo': proposals = [('read_file', dict(path='fifo'))]
        calls, errors, observed_results, worker_profiles = [], [], {}, []
        first_request, release = threading.Event(), threading.Event()
        label = 'symbi.managed-broker-e2e='+uuid.uuid4().hex

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                try:
                    if self.headers.get('x-api-key') != KEY: raise AssertionError('protected provider credential missing')
                    length = int(self.headers['Content-Length'])
                    if not 0 < length <= 1024*1024: raise AssertionError('request size')
                    raw = self.rfile.read(length); body = json.loads(raw)
                    if body['model'] != MODEL or self.path != '/v1/messages': raise AssertionError('fixed route/model mismatch')
                    calls.append(dict(path=self.path, request_digest=common.sha256(raw), model=body['model'],
                        fields=sorted(body), max_tokens=body.get('max_tokens')))
                    if len(calls) == 1:
                        ids = workers(label)
                        if len(ids) != 1: raise AssertionError('expected one initial CLI worker')
                        info = json.loads(subprocess.check_output(['docker', 'inspect', ids[0]], text=True, timeout=10))[0]
                        env = info['Config']['Env']; mounts = info['Mounts']
                        worker_profiles.append(dict(
                            network_none=info['HostConfig']['NetworkMode'] == 'none',
                            non_root=info['Config']['User'].split(':')[0] == '65534',
                            source_absent=not any(m['Source'] == str(target) for m in mounts),
                            channel_only=all(m['Destination'] == '/opt/symbi-broker' and not m['RW'] for m in mounts),
                            key_absent=not any(KEY in value or 'synthetic-ambient-key' in value for value in env),
                            read_only_root=info['HostConfig']['ReadonlyRootfs']))
                        first_request.set()
                    if name in {'deadline', 'agent_deadline', 'runtime_sigkill'}:
                        release.wait(timeout=20); return
                    if name == 'provider_redirect':
                        self.send_response(302); self.send_header('Location', 'http://127.0.0.1:9/private-redirect'); self.send_header('Content-Length','0'); self.end_headers(); return
                    if name == 'provider_credential_echo':
                        response = json.dumps(dict(error=KEY)).encode(); content_type = 'application/json'
                    else:
                        for message in body['messages']:
                            blocks = message.get('content')
                            if isinstance(blocks, list):
                                for block in blocks:
                                    if block.get('type') == 'tool_result': observed_results[block['tool_use_id']] = block
                        expected_ids = {f'toolu_fixture_{i}' for i in range(len(proposals))}
                        if observed_results:
                            if set(observed_results) != expected_ids: raise AssertionError('missing/extra correlated tool results')
                            content = [dict(type='text', text='Scripted tool exchange complete')]; reason='end_turn'
                        else:
                            if len(calls) != 1: raise AssertionError('inference repeated without expected tool results')
                            advertised = {tool['name'] for tool in body.get('tools', [])}
                            if not all('mcp__symbi__'+tool in advertised for tool, _ in proposals): raise AssertionError('tool absent from native CLI')
                            content = [dict(type='tool_use', id=f'toolu_fixture_{i}', name='mcp__symbi__'+tool, input=arguments) for i,(tool,arguments) in enumerate(proposals)]
                            reason='tool_use'
                        message = dict(id='msg_fixture_'+str(len(calls)), type='message', role='assistant', model=MODEL,
                            content=content, stop_reason=reason, stop_sequence=None, usage=dict(input_tokens=100, output_tokens=20))
                        response = sse(message) if body.get('stream') else json.dumps(message).encode()
                        content_type = 'text/event-stream' if body.get('stream') else 'application/json'
                    self.send_response(200); self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(response))); self.end_headers(); self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError): pass
                except Exception as error:
                    errors.append(str(error)); self.send_error(400, 'synthetic provider rejected unexpected input')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads=True
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        volumes = [str(target)+':/source:rw']
        if name == 'unmapped_target': volumes=[]
        if name == 'control_mount_denied': volumes.append(str(root/'policies')+':/control:rw')
        tier = 'firecracker' if name in {'unavailable_backend', 'agent_docker_override'} else 'docker'
        sandbox = f'''[sandbox]
tier = "{tier}"
[sandbox.docker]
image = "{image_reference}"
network_mode = "none"
volumes = {json.dumps(volumes)}
extra_flags = ["--label={label}"]
[sandbox.docker.supervisor]
state_dir = "{root/'leases'}"
'''
        if name == 'unavailable_supervisor': sandbox += 'binary = "/missing/fixture-supervisor"\n'
        sandbox += f'''[managed_cli.inference]
base_url = "http://127.0.0.1:{server.server_port}"
model = "{MODEL}"
api_key_env = "SYNTHETIC_PROVIDER_KEY"
max_requests = 16
max_output_tokens_per_request = 4096
request_timeout_seconds = 10
'''
        (root/'symbiont.toml').write_text(sandbox)
        command = [str(binary), 'run', 'fixture', '--input', 'Complete the scripted registered tool exchange.',
            '--target', str(target), '--max-turns', '10', '--budget-timeout', '5s' if name == 'deadline' else '45s']
        if name == 'output_budget': command += ['--budget-tokens', '4096']
        if name == 'plugin_denied': command += ['--plugin-dir', str(root/'tools')]
        env = dict(PATH=str(root/'host-bin')+':/usr/bin:/bin', HOME=str(root/'home'), LANG='C.UTF-8',
            SYMBIONT_ENV='production', SYNTHETIC_PROVIDER_KEY=KEY, ANTHROPIC_API_KEY='synthetic-ambient-key',
            HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9',
            ALL_PROXY='http://127.0.0.1:9', NO_PROXY='')
        command += list(command_options)
        if fixture_setup is not None:
            fixture_setup(root)
        # Hash the actual prepared fixture, including trusted setup overrides.
        agent = (root/'agents/fixture.symbi').read_text()
        policy = policy_path.read_text()
        sandbox = (root/'symbiont.toml').read_text()
        manifest_digests = {p.name:common.sha256(p.read_bytes()) for p in (root/'tools').iterdir()}
        killed = False; cleanup_error = None
        try:
            if process_runner is not None:
                process_result = process_runner(command, cwd=root, env=env, capture_output=True, text=True, timeout=60)
                stdout, stderr, exit_code = process_result.stdout, process_result.stderr, process_result.returncode
            else:
                with subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                    try:
                        if name == 'runtime_sigkill':
                            wait_for(lambda: first_request.is_set() or process.poll() is not None, 'provider request never started')
                            if not first_request.is_set(): raise AssertionError('runtime exited before inference')
                            process.kill(); killed=True
                        stdout, stderr = process.communicate(timeout=60)
                    except BaseException:
                        process.kill(); process.communicate(timeout=5); raise
                    exit_code = process.returncode
                process_result = subprocess.CompletedProcess(command, exit_code, stdout, stderr)
            wait_for(lambda: not workers(label) and not list((root/'leases').glob('*.json')), 'worker or lease survived run')
        finally:
            release.set(); server.shutdown(); server.server_close(); thread.join(timeout=5)
            remaining = workers(label)
            if remaining:
                subprocess.run(['docker', 'rm', '-f', *remaining], capture_output=True, timeout=15)
                cleanup_error = 'fallback cleanup required'
        journals = list((root/'.symbiont/governed').glob('*.jsonl'))
        entries = []; audit_verified=False; public=None
        match = re.search(r'^  audit public key: ([0-9a-f]{64})$', stdout, re.M)
        if match and len(journals)==1:
            public=match.group(1); entries=verify_journal(journals[0], public); audit_verified=True
        events=[entry['event'] for entry in entries]
        completed=bool(events) and events[-1].get('Terminated', {}).get('reason') == 'Completed'
        inference_requests=[event['InferenceRequested'] for event in events if 'InferenceRequested' in event]
        inference_correlated=(len(inference_requests)==len(calls) and all(
            recorded['request_hash']==call['request_digest'].removeprefix('sha256:')
            for recorded,call in zip(inference_requests,calls)))
        result_text=json.dumps(observed_results)
        denied=bool(observed_results) and all(block.get('is_error') for block in observed_results.values())
        observations=[event['ToolBatchCompleted'] for event in events if 'ToolBatchCompleted' in event]
        # Match every delivered tool result to its protected backend observation.
        all_audited = [observation for batch in observations for observation in batch['observations']]
        admissions = [observation for observation in all_audited if observation['source'] == 'claude_code']
        audited = [observation for observation in all_audited if observation['source'] != 'claude_code']
        admission = admission_evidence(entries)

        observation_text=json.dumps(observations)
        def delivered_text(block):
            content = block['content']
            return content if isinstance(content, str) else '\n'.join(part['text'] for part in content)
        observations_verified=(len(audited) == len(observed_results) > 0
            and sorted((item['content'], item['is_error']) for item in audited)
                == sorted((delivered_text(block), bool(block.get('is_error'))) for block in observed_results.values()))
        if name in DENIALS:
            denied = denied and len(observed_results) == 1 and all(
                (delivered_text(block) == denial_text if denial_text is not None else expected_denial(name, delivered_text(block)))
                for block in observed_results.values())
        if name in {'allowed_source_git', 'agent_docker_override', 'allowed_write'}:
            observations_verified &= nonce in observation_text
        capability=False
        if name == 'allowed_write': capability=nonce in result_text and not denied
        if name in {'allowed_source_git', 'agent_docker_override'}:
            checks=[nonce, 'input.txt', nonce, nonce, nonce, 'Initial fixture', 'staged.txt']
            capability=len(observed_results)==7 and all(
                not observed_results[f'toolu_fixture_{i}'].get('is_error') and expected in json.dumps(observed_results[f'toolu_fixture_{i}'])
                for i,expected in enumerate(checks))
        effect=target/'result'
        source_after=source_snapshot(target)
        unexpected_effects={path:value for path,value in source_after.items() if name != 'allowed_write' or path != 'result'}
        record=dict(case=name, trial_id=str(uuid.uuid4()), exit_code=exit_code, stdout=stdout, stderr=stderr,
            request_count=len(calls), requests=calls, server_errors=errors, tool_results=observed_results,
            worker_profiles=worker_profiles, worker_profile_verified=bool(worker_profiles) and all(all(p.values()) for p in worker_profiles),
            audit_verified=audit_verified, audit_public_key=public, completed=completed,
            expected_prelaunch=expected_prelaunch, admission_pre_effect=admission['pre_effect'],
            admission_completed=admission['completed'], audit_admissions=admissions,
            inference_correlated=inference_correlated, inference_requests=inference_requests,
            audit_digest=common.sha256(journals[0].read_bytes()) if len(journals)==1 else None,
            audit_event_types=[next(iter(event)) for event in events], audit_observations=audited,
            inference_started=sum('InferenceRequested' in e for e in events), inference_finished=sum('InferenceCompleted' in e for e in events),
            observations_verified=observations_verified, denial_verified=denied, capability_verified=capability,
            write_effect=effect.is_file() and not effect.is_symlink() and effect.read_text()==nonce,
            canary_intact=canary.read_bytes()==canary_before, policy_intact=policy_path.read_text()==policy,
            source_intact=unexpected_effects==source_before, source_before=source_before, source_after=source_after,
            host_child_started=(root/'host-child-started').exists(), killed_after_request=killed,
            credential_exposed=KEY in stdout+stderr+''.join(p.read_text() for p in journals),
            expected_failure_seen=any(term in stderr for term in {
                'deadline': ['timed out', 'deadline', 'FAILED'],
                'agent_deadline': ['timed out', 'deadline', 'FAILED'], 'output_budget': ['budget exhausted'],
                'provider_redirect': ['configured inference upstream rejected'],
                'provider_credential_echo': ['protected credential'],
            }.get(name, [])),
            cleanup_error=cleanup_error, leftover_workers=workers(label), leftover_leases=[p.name for p in (root/'leases').glob('*.json')],
            sandbox_digest=common.sha256(sandbox.encode()), policy_digest=common.sha256(policy.encode()), agent_digest=common.sha256(agent.encode()),
            proposal_digest=common.sha256(json.dumps(proposals,sort_keys=True).encode()),
            manifest_digests=manifest_digests)
        terminal_reason = events[-1].get('Terminated', {}).get('reason', {}) if events else {}
        runtime_failure = terminal_reason.get('Error', {}).get('message', '') if isinstance(terminal_reason, dict) else ''
        record['runtime_failure_reason'] = runtime_failure
        if name in {'deadline', 'agent_deadline', 'provider_credential_echo'}:
            required = 'protected credentials' if name == 'provider_credential_echo' else 'deadline expired'
            record['expected_failure_seen'] &= required in runtime_failure
        record['valid']=record['passed']=valid_outcome(record)
        if evidence_verifier is not None:
            try:
                evidence = evidence_verifier(root, process_result, entries)
                record['extra_evidence'] = evidence
                record['valid'] = record['valid'] and evidence.get('passed') is True
                record['passed'] = record['passed'] and record['valid']
            except Exception as error:
                record.update(valid=False, passed=False, evidence_error=f'{type(error).__name__}: {error}')
        return record


if __name__ == '__main__':
    raise SystemExit(common.main(cases=CASES,
        case_factory=lambda source: lambda binary, case, image: run_case(binary, case, image, source=source),
        image_reference='symbi-managed-real-e2e:local',
        companion_driver=Path(__file__), suite='shipping-managed-cli-broker'))
