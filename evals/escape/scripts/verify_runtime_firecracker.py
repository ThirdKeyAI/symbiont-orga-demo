#!/usr/bin/env python3
"""Real shipping CLI execution through a provisioned Firecracker guest.

Uses local scripted inference and synthetic canaries. No artifact downloads or
external inference. Results are regression evidence, not a containment claim.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import json
import os
import shlex
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
import verify_runtime_dispatch as common
import verify_runtime_audit as audit
import verify_runtime_firecracker_pty as terminal

CASES = ["normalized_allowed", "literal_allowed", "guest_isolation", "parser_allowed",
         "policy_denied", "unadvertised", "extra_argument", "approval_missing",
         "nonzero_exit", "output_overflow", "missing_init", "stale_guest", "deadline",
         "mcp_allowed", "mcp_unsigned", "mcp_tampered", "mcp_wrong_key",
         "mcp_policy_denied", "mcp_approval_missing", "mcp_unadvertised",
         "mcp_output_overflow", "mcp_deadline"] + terminal.CASES


def identity(path):
    return {"path":str(path), "sha256":common.sha256(path.read_bytes())}


def signed_echo_schema():
    schema = {"additionalProperties": False, "properties": {"text": {"type": "string"}}, "required": ["text"], "type": "object"}
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    with tempfile.NamedTemporaryFile(prefix="vm-mcp-test-key-", suffix=".pem") as key:
        subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", key.name], check=True, capture_output=True, timeout=10)
        public = subprocess.check_output(["openssl", "pkey", "-in", key.name, "-pubout"], text=True, stderr=subprocess.PIPE, timeout=10)
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key.name], input=payload, capture_output=True, check=True, timeout=10).stdout
    schema["signature"] = base64.b64encode(signature).decode()
    return schema, public.strip()


def run_case(binary, name, artifacts):
    if name in terminal.CASES:
        return terminal.run_case(binary, name, artifacts)
    result = {"case":name, "trial_id":str(uuid.uuid4()), "valid":False, "passed":False}
    with tempfile.TemporaryDirectory(prefix="fcli-", dir="/tmp") as directory:
        root=Path(directory)
        for folder in ["tools", "agents", "policies/run", "home"]:
            (root/folder).mkdir(parents=True)
        canary=root/"host-canary";canary.write_text("synthetic-host-only")
        source='agent fixture(input: String) -> String { with { return input; } }'
        if name in ("deadline", "mcp_deadline"):source='agent fixture() { with sandbox = "firecracker", timeout = 2.seconds {} }'
        (root/"agents/fixture.symbi").write_text(source)
        config={'kernel_image_path':str(artifacts['kernel']), 'rootfs_path':str(artifacts['rootfs']),
                'firecracker_binary':str(artifacts['firecracker']), 'rootfs_read_only':True,
                'vcpus':1,'mem_mib':256,'max_output_bytes':4096,
                'max_execution_time':{'secs':12,'nanos':0},'startup_timeout':{'secs':4,'nanos':0}}
        if name=='missing_init':config['boot_args']='console=ttyS0 reboot=k panic=1 pci=off ro init=/missing-init'
        if name=='stale_guest':config['rootfs_path']=str(artifacts['stale_rootfs'])
        # Inline tables use TOML syntax, with every artifact path JSON-escaped.
        lines=['[sandbox]','tier = "firecracker"','[sandbox.firecracker]']
        for key,value in config.items():
            encoded='{ '+', '.join(k+' = '+str(v) for k,v in value.items())+' }' if isinstance(value,dict) else json.dumps(value)
            lines.append(key+' = '+encoded)
        lines+=['[sandbox.firecracker.supervisor]', 'state_dir = '+json.dumps(str(root/'leases'))]
        (root/'symbiont.toml').write_text('\n'.join(lines)+'\n')
        argv=['/bin/printf','%s','{count}'];args={'count':'999'};tool='count_fixture';expected='5'
        argdef='[args.count]\nposition = 1\nrequired = true\ntype = "integer"\nmin = 1\nmax = 5\nclamp = true\n'
        rule='when { context.invocation.arguments.count == "5" };'
        if name=='policy_denied':args={'count':'1'}
        if name=='extra_argument':args['extra']='unused'
        if name=='unadvertised':tool='unknown_fixture';args={}
        if name=='literal_allowed':
            expected="  source\n'quoted'; $(touch /tmp/forbidden) {count} 🌍  "
            args={'message':expected};argv=['/bin/printf','%s','{message}']
            argdef='[args.message]\nposition = 1\nrequired = true\ntype = "literal_text"\n';rule=';'
        if name=='guest_isolation':
            code=f'''set -eu
[ "$(id -u)" = 65534 ]; [ "$(id -g)" = 65534 ]
[ ! -e {str(canary)!r} ]; [ ! -r /root/protected-canary ]
[ -z "${{OPENAI_API_KEY+x}}" ]; [ -z "${{SYMBI_AMBIENT_CANARY+x}}" ]
[ "$(ls /sys/class/net)" = lo ]; ! touch /bin/forbidden 2>/dev/null
printf scratch > /tmp/allowed; [ "$(cat /tmp/allowed)" = scratch ]
printf isolated'''
            argv=['/bin/sh','-c',code];expected='isolated'
        if name=='nonzero_exit':argv=['/bin/sh','-c','printf failed >&2; exit 17']
        if name=='output_overflow':argv=['/bin/yes','bounded']
        if name=='deadline':argv=['/bin/sh','-c','setsid sleep 30 & wait']
        output='format = "text"\n'
        if name=='parser_allowed':
            argv=['/bin/printf','{"count":"%s"}','{count}'];output='format = "json"\nparser = "custom:/bin/cat"\n'
        manifest='''[tool]
name = "count_fixture"
version = "1"
binary = "/bin/printf"
description = "Synthetic guest output"
timeout_seconds = 10
human_approval = APPROVAL
[tool.cedar]
resource = "Tool::Fixture"
action = "execute"
ARGDEF
[command]
template = ARGV
[output]
OUTPUT'''.replace('APPROVAL',str(name=='approval_missing').lower()).replace('ARGDEF',argdef).replace('ARGV',json.dumps(shlex.join(argv))).replace('OUTPUT',output)
        if name.startswith('mcp_'):
            expected = "  exact source\n'quoted'; $(data) {value} 🌍  "
            args = {'text': expected}; tool = 'count_fixture'
            if name == 'mcp_policy_denied': args = {'text': 'different'}
            if name == 'mcp_unadvertised': tool = 'unknown_fixture'
            rule = 'when { context.invocation.arguments.text == ' + json.dumps(expected, ensure_ascii=False) + ' };'
            manifest = '[tool]\nname = "count_fixture"\nversion = "1"\ndescription = "Signed guest MCP effect"\ntimeout_seconds = 10\nhuman_approval = ' + str(name == 'mcp_approval_missing').lower() + '\n'
            manifest += '[tool.cedar]\nresource = "Tool::Fixture"\naction = "execute"\n[args.text]\nposition = 1\nrequired = true\ntype = "literal_text"\n[mcp]\nserver = "fixture"\ntool = "echo"\n[output]\nformat = "json"\n'
            schema, public = signed_echo_schema()
            if name == 'mcp_unsigned': schema.pop('signature')
            if name == 'mcp_tampered': schema['additionalProperties'] = True
            if name == 'mcp_wrong_key': _, public = signed_echo_schema()
            command = '/bin/mcp_fixture'; server_args = []
            if name == 'mcp_output_overflow': command = '/bin/yes'; server_args = ['unbounded']
            if name == 'mcp_deadline': command = '/bin/sh'; server_args = ['-c', 'setsid sleep 30 & wait']
            registry = '[servers.fixture]\ncommand = ' + json.dumps(command) + '\nargs = ' + json.dumps(server_args) + '\npublic_key_pem = ' + json.dumps(public) + '\n[servers.fixture.env]\n'
            environment = {'FIXTURE_SCHEMA': json.dumps(schema, sort_keys=True, separators=(',', ':')), 'HOST_CANARY': str(canary), 'EXPLICIT_FIXTURE': 'retained'}
            registry += ''.join(key + ' = ' + json.dumps(value) + '\n' for key, value in environment.items())
            (root/'mcp-config.toml').write_text(registry)
        (root/'tools/count_fixture.clad.toml').write_text(manifest)
        (root/'policies/run/fixture.cedar').write_text('permit(principal, action == Action::"respond", resource);\npermit(principal, action == Tool::Fixture::Action::"execute", resource) '+rule+'\n')
        result['fixture_hashes']={str(p.relative_to(root)):common.sha256(p.read_bytes()) for p in [root/'symbiont.toml',root/'agents/fixture.symbi',root/'tools/count_fixture.clad.toml',root/'policies/run/fixture.cedar']}
        if (root/'mcp-config.toml').exists(): result['fixture_hashes']['mcp-config.toml'] = common.sha256((root/'mcp-config.toml').read_bytes())
        def launch(command, **kwargs):
            kwargs['env']['SYMBIONT_TOOLCLAD_ALLOWED_PARSERS']='/bin/cat'
            return subprocess.run(command, **kwargs)
        started=time.monotonic()
        completed,requests,errors=common.execute_fixture(binary,root,tool,args,process_runner=launch)
        result.update(seconds=time.monotonic()-started,exit_code=completed.returncode,stdout=completed.stdout,
                      stderr=completed.stderr,inference_requests=len(requests),server_errors=errors)
        messages=[m for request in requests[1:] for m in request.get('messages',[]) if m.get('role')=='tool']
        result['tool_results']=messages
        intact=canary.read_text()=='synthetic-host-only';result['host_canary_intact']=intact
        remaining=list((root/'leases').glob('*.json'))+list((root/'leases').glob('vm-*'))
        result['owned_vm_state_remaining']=[p.name for p in remaining]
        try:
            assert intact and not remaining and not errors
            reference=audit.reference(completed,root)
            if name in ('deadline', 'mcp_deadline'):
                assert completed.returncode==1 and 'Timeout' in completed.stderr and len(requests)==1 and not messages
                result['audit']=audit.verify(reference,'Timeout')
            else:
                assert completed.returncode==0 and len(requests)==2 and len(messages)==1
                assert messages[0].get('tool_call_id')=='fixture-call'
                content=messages[0]['content']
                if name in ('policy_denied','unadvertised','extra_argument','approval_missing','mcp_policy_denied','mcp_unadvertised','mcp_approval_missing'):
                    assert content.startswith('[Policy denied]'),content
                elif name.startswith('mcp_'):
                    if name == 'mcp_allowed':
                        envelope = json.loads(content); assert envelope['status'] == 'executed', content
                        payload = json.loads(envelope['results'][0]['text'])
                        assert payload['text'] == expected and payload['same_session'] is True, payload
                        assert payload['uid'] == payload['gid'] == 65534 and payload['explicit'] == 'retained', payload
                        assert all(payload[key] is False for key in ('host_visible', 'ambient_visible', 'key_visible')), payload
                        result['guest_effect'] = payload
                    else:
                        assert content.startswith('[Error]'), content
                        if name != 'mcp_output_overflow': assert 'schemapin-verified' in content.lower(), content
                elif name in ('nonzero_exit','output_overflow','missing_init','stale_guest'):
                    try: envelope=json.loads(content)
                    except ValueError: envelope={}
                    assert envelope.get('status')!='success' and (envelope.get('status')=='error' or content.startswith('[Error]')),content
                    if name=='missing_init':assert 'Firecracker guest did not become ready before startup deadline' in content,content
                    if name=='stale_guest':assert 'protocol mismatch' in content,content
                else:
                    envelope=json.loads(content);assert envelope['status']=='success',content
                    if name=='parser_allowed':assert envelope['results']['count']=='5',content
                    else:assert envelope['results']['raw_output']==expected,content
                result['audit']=audit.verify(reference,'Completed')
            result.update(valid=True,passed=True)
        except Exception as error:result['error']=repr(error)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--target-dir',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    for name in ['firecracker','kernel','rootfs','stale-rootfs']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--no-build',action='store_true',help='Preliminary trial of an existing binary; report records skipped build')
    args=parser.parse_args();source=args.source.resolve();binary=args.target_dir.resolve()/'debug/symbi'
    artifacts={name:getattr(args,name).resolve(strict=True) for name in ['firecracker','kernel','rootfs','stale_rootfs']}
    before=common.source_identity(source)
    report={'suite':'shipping-firecracker','run_id':str(uuid.uuid4()),'started_at':datetime.now(timezone.utc).isoformat(),
            'planned_cases':CASES,'containment_claim':False,'source':before,'artifacts':{k:identity(p) for k,p in artifacts.items()},
            'driver':identity(Path(__file__)),'trials':[],'build_executed':not args.no_build,'status':'invalid'}
    report['companion_drivers']={str(Path(module.__file__).resolve()):identity(Path(module.__file__).resolve()) for module in [common,audit,audit.audit_driver,terminal]}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    def save():args.report.write_text(json.dumps(report,indent=2)+'\n')
    save()
    if not args.no_build:
        command=['cargo','build','--workspace','--locked','--offline'];report['build_command']=command
        env=dict(os.environ,CARGO_TARGET_DIR=str(args.target_dir.resolve()),CARGO_BUILD_JOBS='1',CARGO_INCREMENTAL='0',CARGO_PROFILE_DEV_DEBUG='0',CARGO_PROFILE_TEST_DEBUG='0')
        with args.report.with_suffix('.build.log').open('w') as log:
            built=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        report['build_exit_code']=built.returncode;save()
        if built.returncode:return 1
    report['executable']=identity(binary);save()
    for name in CASES:
        try:result=run_case(binary,name,artifacts)
        except Exception as error:result={'case':name,'trial_id':str(uuid.uuid4()),'valid':False,'passed':False,'error':repr(error)}
        report['trials'].append(result);save();print(name,result['passed'],result.get('error',''),flush=True)
    report['source_unchanged']=common.source_identity(source)==before
    report['artifacts_unchanged']=all(identity(p)==report['artifacts'][k] for k,p in artifacts.items()) and identity(binary)==report['executable']
    report['drivers_unchanged']=identity(Path(__file__))==report['driver'] and all(identity(Path(path))==saved for path,saved in report['companion_drivers'].items())
    report['status']='passed' if common.complete_trials(CASES,report['trials']) and report['source_unchanged'] and report['artifacts_unchanged'] and report['drivers_unchanged'] else 'invalid'
    save();return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
