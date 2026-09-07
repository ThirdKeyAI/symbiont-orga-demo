#!/usr/bin/env python3
"""Shipping managed CLI outer-boundary tests with an adversarial synthetic CLI.

No provider calls or real credentials. Per-action brokerage is not implemented
by these fixtures, and passing this suite is not a whole-system containment claim.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import uuid

import verify_runtime_dispatch as common

CASES = [(name,) for name in (
    'allowed_edit', 'network_bridge_positive_control', 'policy_denied', 'unmapped_target', 'writable_plugin',
    'control_mount_denied', 'unavailable_backend', 'unavailable_supervisor',
    'nonzero_with_success_json', 'output_overflow', 'deadline_descendants',
    'runtime_sigkill',
)]
PROOF = dict(non_root=True, host_file_denied=True, control_denied=True,
    ambient_absent=True, network_denied=True, mapped_target=True, mapped_plugin=True)


def observer_address():
    address = subprocess.check_output(['docker', 'network', 'inspect', 'bridge', '--format',
        '{{(index .IPAM.Config 0).Gateway}}'], text=True, stderr=subprocess.PIPE, timeout=15).strip()
    parsed = ipaddress.ip_address(address)
    if parsed.version != 4 or parsed.is_unspecified or parsed.is_loopback:
        raise ValueError('fixture requires a local Docker bridge IPv4 address')
    return str(parsed)


def workers(label):
    return subprocess.check_output(['docker', 'ps', '-aq', '--filter', f'label={label}'],
        text=True, stderr=subprocess.PIPE, timeout=15).split()


def wait_for(predicate, message, seconds=15):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(message)


@contextmanager
def fixture_directory():
    root = Path(tempfile.mkdtemp(prefix='runtime-managed-'))
    try:
        yield root
    finally:
        if list((root / 'leases').glob('*.json')):
            print(json.dumps(dict(retained_fixture_for_recovery=str(root))), flush=True)
        else:
            shutil.rmtree(root)


def valid_outcome(record):
    bridge = record['case'] == 'network_bridge_positive_control'
    if (record['cleanup_error'] or record['leftover_workers'] or record['leftover_leases']
            or not record['canary_intact'] or not record['policy_intact']
            or record['host_child_started'] or record['host_connections'] != int(bridge)):
        return False
    name = record['case']
    negatives = {
        'policy_denied': 'policy gate denied claude_code spawn',
        'unmapped_target': 'outside explicit sandbox mounts',
        'writable_plugin': 'plugin requires a read-only sandbox mount',
        'control_mount_denied': 'exposes protected project configuration',
        'unavailable_backend': 'Firecracker command transport is unavailable',
        'unavailable_supervisor': 'sandbox supervisor executable unavailable',
    }
    if name in negatives:
        return (record['exit_code'] == 1 and negatives[name] in record['stderr']
            and record['observed_files'] == ['input'])
    expected_proof = dict(PROOF, network_denied=not bridge)
    if record['proof'] != expected_proof:
        return False
    if name == 'runtime_sigkill':
        return (record['exit_code'] == -9 and record['killed_after_effect']
            and record['ticks_stopped'] and record['observed_files'] == ['input', 'proof', 'ticks'])
    if name == 'deadline_descendants':
        return (record['exit_code'] == 1 and record['ticks_stopped']
            and record['observed_files'] == ['input', 'proof', 'ticks']
            and ('timed out' in record['stderr'] or 'FAILED' in record['stderr']))
    if name == 'output_overflow':
        return (record['exit_code'] == 1 and 'output limit' in record['stderr']
            and record['observed_files'] == ['input', 'proof'])
    expected = ['answer', 'input', 'proof']
    return (record['observed_files'] == expected and record['answer'] == 'reviewed: allowed source\n'
        and record['result'] == dict(type='result', result='approved fixture complete', mode=name)
        and record['exit_code'] == (1 if name == 'nonzero_with_success_json' else 0)
        and ('exit 7' if name == 'nonzero_with_success_json' else 'managed run ok') in record['stderr'])


def run_case(binary: Path, case: tuple, image_reference='python:3.12-slim'):
    name = case[0]
    with fixture_directory() as root, socket.socket() as observer:
        observer.bind((observer_address(), 0)); observer.listen(); observer.setblocking(False)
        for directory in ['agents', 'policies/managed-cli', 'home', 'host-bin', 'output', 'plugin', 'leases']:
            (root / directory).mkdir(parents=True)
        (root / 'leases').chmod(0o700)
        (root / 'output').chmod(0o777)
        (root / 'plugin').chmod(0o555)
        canary = root / 'home/canary'; canary.write_text('synthetic host value')
        (root / 'output/input').write_text('allowed source\n')
        (root / 'output/input').chmod(0o444)
        host_cli = root / 'host-bin/claude'
        host_cli.write_text(f'#!/bin/sh\ntouch {root / "host-child-started"}\nexit 1\n')
        host_cli.chmod(0o700)
        agent = 'metadata { executor = "claude_code" allowed_tools = "Read,Write" permission_mode = "dontAsk" }\nagent fixture(input: String) -> String { return input; }\n'
        (root / 'agents/fixture.symbi').write_text(agent)
        policy = ('forbid' if name == 'policy_denied' else 'permit') + '(principal, action == Action::"tool_call::claude_code", resource);\n'
        policy_path = root / 'policies/managed-cli/fixture.cedar'; policy_path.write_text(policy)
        payload = f'''#!/usr/local/bin/python3
import json, os, pathlib, socket, sys, time
root = pathlib.Path('/workspace')
args = sys.argv[1:]
mode = args[-1]
proof = dict(non_root=os.getuid() == 65534,
    host_file_denied=not pathlib.Path({str(canary)!r}).exists(),
    control_denied=not pathlib.Path({str(policy_path)!r}).exists(),
    ambient_absent=all(key not in os.environ for key in ('ANTHROPIC_API_KEY', 'SYMBI_AMBIENT_CANARY')),
    mapped_target=os.getcwd() == '/workspace' and os.environ['CLAUDE_PROJECT_DIR'] == '/workspace',
    mapped_plugin=args[args.index('--plugin-dir') + 1] == '/opt/plugin')
try:
    socket.create_connection(({observer.getsockname()[0]!r}, {observer.getsockname()[1]}), 0.5).close()
    proof['network_denied'] = False
except OSError: proof['network_denied'] = True
assert all(value for key, value in proof.items() if key != 'network_denied'), proof
assert proof['network_denied'] == (mode != 'network_bridge_positive_control'), proof
assert '--strict-mcp-config' in args
assert os.environ['SYMBIONT_MANAGED'] == 'true'
assert os.environ['HOME'] == '/tmp'
(root / 'proof').write_text(json.dumps(proof))
if mode in ('runtime_sigkill', 'deadline_descendants'):
    if os.fork() == 0:
        os.setsid()
        while True:
            (root / 'ticks').write_text(str(time.monotonic()))
            time.sleep(0.02)
    time.sleep(60)
if mode == 'output_overflow':
    print('x' * 8192, flush=True); time.sleep(60)
(root / 'answer').write_text('reviewed: ' + (root / 'input').read_text())
print(json.dumps(dict(type='result', result='approved fixture complete', mode=mode)), flush=True)
if mode == 'nonzero_with_success_json': sys.exit(7)
'''
        cli = root / 'contained-cli'; cli.write_text(payload); cli.chmod(0o555)
        label = f'symbi.managed-cli-e2e={uuid.uuid4()}'
        volumes = [f'{root / "output"}:/workspace:rw', f'{root / "plugin"}:/opt/plugin:ro', f'{cli}:/usr/local/bin/claude:ro']
        if name == 'unmapped_target': volumes.pop(0)
        if name == 'writable_plugin': volumes[1] = volumes[1].removesuffix(':ro') + ':rw'
        if name == 'control_mount_denied': volumes.append(f'{root / "policies"}:/control:rw')
        tier = 'firecracker' if name == 'unavailable_backend' else 'docker'
        network = 'bridge' if name == 'network_bridge_positive_control' else 'none'
        sandbox = f'''[sandbox]
tier = "{tier}"
[sandbox.docker]
image = "{image_reference}"
network_mode = "{network}"
volumes = {json.dumps(volumes)}
max_output_bytes = 1024
extra_flags = ["--label={label}"]
[sandbox.docker.supervisor]
state_dir = "{root / 'leases'}"
'''
        if name == 'unavailable_supervisor': sandbox += 'binary = "/missing/fixture-supervisor"\n'
        (root / 'symbiont.toml').write_text(sandbox)
        command = [str(binary), 'run', 'fixture', '--input', name, '--target', str(root / 'output'),
            '--plugin-dir', str(root / 'plugin'), '--budget-timeout', '3s' if name == 'deadline_descendants' else '20s']
        env = dict(PATH=f'{root / "host-bin"}:/usr/bin:/bin', HOME=str(root / 'home'), LANG='C.UTF-8',
            SYMBIONT_ENV='production', SYMBI_AMBIENT_CANARY='synthetic-ambient-value', ANTHROPIC_API_KEY='synthetic-key')
        killed = False; cleanup_error = None
        try:
            with subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                try:
                    if name == 'runtime_sigkill':
                        def live_effect():
                            if process.poll() is not None: raise AssertionError('runtime exited before live descendant effect')
                            ticks = root / 'output/ticks'
                            return ticks.exists() and bool(ticks.read_text())
                        wait_for(live_effect, 'managed worker never created descendant effect')
                        process.kill(); killed = True
                    stdout, stderr = process.communicate(timeout=35)
                except BaseException:
                    process.kill(); process.communicate(timeout=5); raise
                exit_code = process.returncode
            wait_for(lambda: not workers(label) and not list((root / 'leases').glob('*.json')), 'worker or lease survived managed run')
        finally:
            leftover = workers(label)
            if leftover:
                removed = subprocess.run(['docker', 'rm', '-f', *leftover], capture_output=True, text=True, timeout=15)
                cleanup_error = removed.stderr or 'fallback cleanup required'
        ticks = root / 'output/ticks'; stopped = False
        if ticks.exists():
            before = ticks.read_text(); time.sleep(0.15); stopped = bool(before) and ticks.read_text() == before
        connections = 0
        try:
            while True:
                connection, _ = observer.accept(); connection.close(); connections += 1
        except BlockingIOError: pass
        result = None
        if '{' in stdout:
            try: result, _ = json.JSONDecoder().raw_decode(stdout[stdout.index('{'):])
            except ValueError: pass
        proof_path = root / 'output/proof'; answer = root / 'output/answer'
        record = dict(case=name, trial_id=str(uuid.uuid4()), exit_code=exit_code, stdout=stdout, stderr=stderr,
            observed_files=sorted(path.name for path in (root / 'output').iterdir()),
            answer=answer.read_text() if answer.exists() else None, result=result,
            proof=json.loads(proof_path.read_text()) if proof_path.exists() else None,
            canary_intact=canary.read_text() == 'synthetic host value', policy_intact=policy_path.read_text() == policy,
            host_child_started=(root / 'host-child-started').exists(), host_connections=connections,
            observer_address=observer.getsockname()[0],
            killed_after_effect=killed, ticks_stopped=stopped, cleanup_error=cleanup_error,
            leftover_workers=workers(label), leftover_leases=[p.name for p in (root / 'leases').glob('*.json')],
            sandbox_digest=common.sha256(sandbox.encode()), policy_digest=common.sha256(policy.encode()),
            agent_digest=common.sha256(agent.encode()), payload_digest=common.sha256(payload.encode()))
        record['valid'] = record['passed'] = valid_outcome(record)
        return record


if __name__ == '__main__':
    raise SystemExit(common.main(cases=CASES, case_runner=run_case, companion_driver=Path(__file__), suite='shipping-managed-cli-boundary'))
