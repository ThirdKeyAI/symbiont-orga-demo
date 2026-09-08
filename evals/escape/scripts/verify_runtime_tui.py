#!/usr/bin/env python3
"""Drive the shipping shell's Gate view using a private terminal and local API."""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import verify_runtime_dispatch as common

CASES = [(name,) for name in ('approved', 'denied', 'resolution_denied', 'reordered',
    'changed', 'removed', 'expired', 'oversized', 'controls', 'refresh_failed', 'no_review')]


def request(identifier, *, note='reviewed-value', timeout=60):
    now = datetime.now(timezone.utc)
    return dict(id=identifier, agent_id='operator-fixture', kind='tool_call',
        summary='tool_call edit_file', reason='exact-call approval required',
        context_snapshot={'invocation': {'arguments': {'path': 'allowed/result', 'note': note},
            'resource': {'target': 'allowed/result'}, 'fingerprint': 'fixture-contract'}},
        created_at=now.isoformat(), expires_at=(now+timedelta(seconds=timeout)).isoformat(), status='pending')


class ApprovalServer(ThreadingHTTPServer):
    def __init__(self, items, *, resolution_status=200):
        self.lock = threading.Lock()
        self.items = items
        self.resolution_status = resolution_status
        self.refresh_status = 200
        self.posts = []
        self.snapshots = []
        self.polls = 0
        self.token = 'synthetic-' + uuid.uuid4().hex
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.headers.get('Authorization') != 'Bearer ' + owner.token:
                    return self.respond(401, {'error': 'synthetic authorization required'})
                with owner.lock:
                    if self.path == '/api/v1/health':
                        return self.respond(200, {})
                    if self.path == '/api/v1/approvals':
                        owner.polls += 1
                        body = owner.items if owner.refresh_status == 200 else {'error':'refresh refused'}
                        digest = common.sha256(json.dumps(body).encode())
                        if not owner.snapshots or owner.snapshots[-1]['digest'] != digest:
                            owner.snapshots.append(dict(status=owner.refresh_status, digest=digest, body=copy.deepcopy(body)))
                        return self.respond(owner.refresh_status, body)
                self.respond(404, {})

            def do_POST(self):
                with owner.lock:
                    status = owner.resolution_status
                    authenticated = self.headers.get('Authorization') == 'Bearer ' + owner.token
                    if not authenticated:
                        status = 401
                    matches = [item for item in owner.items if self.path in (
                        '/api/v1/approvals/' + item['id'] + '/approve',
                        '/api/v1/approvals/' + item['id'] + '/deny')]
                    if len(matches) != 1:
                        status = 404
                    elif datetime.fromisoformat(matches[0]['expires_at']) <= datetime.now(timezone.utc):
                        status = 410
                    owner.posts.append(dict(path=self.path, status=status, authenticated=authenticated))
                    if status == 200:
                        owner.items = [item for item in owner.items if item['id'] != matches[0]['id']]
                self.respond(status, {'status':'accepted'} if status == 200 else {'error':'resolution refused'})

        super().__init__(('127.0.0.1', 0), Handler)


def terminal_binary():
    candidate=os.environ.get('SYMBI_E2E_TMUX') or shutil.which('tmux')
    if not candidate or not Path(candidate).is_file():
        raise RuntimeError('tmux is required for the shipping TUI test; set SYMBI_E2E_TMUX for a provisioned binary')
    return str(Path(candidate).resolve())


class TerminalShell:
    """Capture the actual terminal grid through an isolated tmux server."""
    def __init__(self, binary, root):
        self.terminal=terminal_binary()
        self.root=root
        self.socket=root/'tmux.socket'
        self.name='approval-'+uuid.uuid4().hex
        self.env=dict(PATH='/usr/bin:/bin', HOME=str(root), LANG='C.UTF-8', TERM='xterm-256color',
            SHELL='/bin/sh', SYMBIONT_MASTER_KEY='0'*64,
            DBUS_SESSION_BUS_ADDRESS='unix:path=/tmp/symbiont-no-test-keyring')
        self.frames=[]
        self.server_pid=None
        self.pane_pid=None
        try:
            self.control('new-session','-d','-s',self.name,'-x','160','-y','40',
                '-c',str(root),'/usr/bin/env',str(binary))
            self.server_pid=int(self.control('display-message','-p','#{pid}').stdout.strip())
            self.pane_pid=int(self.control('display-message','-p','#{pane_pid}').stdout.strip())
        except BaseException:
            self.close()
            raise

    def control(self, *args, check=True):
        return subprocess.run([self.terminal,'-S',str(self.socket),'-f','/dev/null',*args],
            env=self.env,cwd=self.root,check=check,capture_output=True,text=True,timeout=5)

    def send(self, text):
        self.control('send-keys','-t',self.name+':0.0','-l','--',text)

    def frame(self):
        content=self.control('capture-pane','-t',self.name+':0.0','-p').stdout
        if not self.frames or self.frames[-1]!=content:
            self.frames.append(content)
        if len(self.frames)>500 or sum(len(frame) for frame in self.frames)>2*1024*1024:
            raise RuntimeError('terminal evidence exceeds output limit')
        return content

    def wait(self, predicate, description, timeout=12):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.control('has-session','-t',self.name,check=False).returncode:
                raise RuntimeError('shell exited before '+description)
            if predicate():
                return
            time.sleep(.1)
        raise TimeoutError(description)

    def shown(self, text, timeout=12):
        self.wait(lambda: text in self.frame(), 'display '+text,timeout)

    def details(self, markers):
        for _ in range(30):
            frame=self.frame()
            markers=[marker for marker in markers if marker not in frame]
            if not markers:
                return
            self.send('\x1b[6~')
            time.sleep(.12)
        raise AssertionError('complete review missing '+repr(markers))

    def close(self):
        self.control('kill-server',check=False)
        def alive(pid):
            if pid is None:
                return False
            try:
                state=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[0]
                return state != 'Z'
            except FileNotFoundError:
                return False
        deadline=time.monotonic()+5
        while (alive(self.server_pid) or alive(self.pane_pid)) and time.monotonic()<deadline:
            time.sleep(.05)
        stopped=not alive(self.server_pid) and not alive(self.pane_pid)
        unavailable=self.control('has-session','-t',self.name,check=False).returncode != 0
        if stopped and unavailable:
            self.socket.unlink(missing_ok=True)
        return stopped and unavailable


def run_case(binary, case, _image):
    name=case[0]
    identifier=uuid.uuid4().hex[:16]
    other_id=uuid.uuid4().hex[:16]
    item=request(identifier, timeout=7 if name=='expired' else 60)
    if name=='oversized':
        item['reason']='x'*70_000
    if name=='controls':
        item['context_snapshot']['invocation']['arguments']['note']='\x1b[2J\u202eoperator\u2066\x7f'
    items=[request(other_id),item] if name=='reordered' else [item]
    record=dict(case=name,trial_id=str(uuid.uuid4()), valid=False,passed=False,
        fixture_digest=common.sha256(json.dumps(items,sort_keys=True).encode()), request_id=identifier,
        scope='Shipping terminal UI and authenticated synthetic approval API; no tool execution claim')
    terminal = os.environ.get('SYMBI_E2E_TMUX') or shutil.which('tmux')
    if terminal:
        record['terminal_emulator'] = dict(path=terminal, digest=common.sha256(Path(terminal).read_bytes()),
            version=subprocess.check_output([terminal,'-V'],text=True,timeout=5).strip())
    temporary=tempfile.mkdtemp(prefix='symbi-tui-e2e-')
    root=Path(temporary)
    server=ApprovalServer(copy.deepcopy(items),resolution_status=403 if name=='resolution_denied' else 200)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    shell=None
    try:
        shell=TerminalShell(binary,root)
        shell.shown('symbi')
        shell.send('/attach http://127.0.0.1:'+str(server.server_port)+' --token '+server.token+'\r')
        shell.shown('Attached to')
        shell.send('\x07')
        shell.shown(identifier)
        if name=='reordered':
            shell.send('\x1b[B')
            shell.shown('> '+identifier)
            before=server.polls
            with server.lock:
                server.items.reverse()
            shell.wait(lambda: server.polls>before,'reordered refresh')
            time.sleep(0.2)
        if name=='no_review':
            shell.send('a'); shell.shown('Press Enter to review')
            assert not server.posts
        else:
            shell.send('\r')
            if name=='oversized':
                shell.shown('too large to review')
                shell.send('a'); shell.shown('Press Enter to review')
                assert not server.posts
            else:
                shell.shown('Review '+identifier)
                markers=['allowed/result','fixture-contract']
                if name=='controls':
                    markers+=['\\u001b','\\u202e','\\u2066']
                shell.details(markers)
                assert not server.posts, 'review/navigation must not resolve'
                if name in ('changed','removed','refresh_failed'):
                    before=server.polls
                    with server.lock:
                        if name=='changed':
                            server.items[0]['context_snapshot']['invocation']['arguments']['path']='substituted/path'
                        elif name=='removed':
                            server.items=[]
                        else:
                            server.refresh_status=403
                    shell.wait(lambda: server.polls>before,'changed queue poll')
                    shell.shown('Approval refresh failed' if name=='refresh_failed' else 'Reviewed request changed')
                    shell.send('a'); shell.shown('Press Enter to review')
                    assert not server.posts
                elif name=='expired':
                    shell.shown('Reviewed request changed',timeout=10)
                    shell.send('a'); shell.shown('Press Enter to review')
                    assert not server.posts
                else:
                    shell.send('d' if name=='denied' else 'a')
                    shell.wait(lambda: len(server.posts)>0,'approval API resolution')
                    expected='deny' if name=='denied' else 'approve'
                    assert server.posts==[dict(path='/api/v1/approvals/'+identifier+'/'+expected,
                        status=403 if name=='resolution_denied' else 200,authenticated=True)]
                    shell.shown('HTTP 403' if name=='resolution_denied' else ('Denied '+identifier if name=='denied' else 'Approved '+identifier))
                    shell.send('a')
                    shell.shown('Press Enter to review')
                    assert len(server.posts)==1, 'a completed or failed review cannot be replayed'
        record.update(valid=True,passed=True)
    except Exception as error:
        record['error']=type(error).__name__+': '+str(error)
    finally:
        record['queue_snapshots']=server.snapshots
        record['posts']=server.posts
        record['polls']=server.polls
        if shell is not None:
            record['frames']=shell.frames
            record['terminal_processes']={'server_pid':shell.server_pid,'shell_pid':shell.pane_pid}
            try:
                record['terminal_cleaned']=shell.close()
            except Exception as error:
                record['cleanup_error']=str(error)
                record['terminal_cleaned']=False
        else:
            record['terminal_cleaned']=False
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        record['passed']=record['passed'] and record['terminal_cleaned']
    if record['terminal_cleaned']:
        shutil.rmtree(root)
    else:
        record['artifact_dir']=str(root)
    return record


if __name__=='__main__':
    raise SystemExit(common.main(cases=CASES,case_runner=run_case,companion_driver=Path(__file__),
        suite='shipping-tui-approvals',binary_name='symbi-shell',build_command=['cargo','build','--workspace','--locked','--offline']))
