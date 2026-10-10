"""Focused shipping path contract E2E with protected worker observation."""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harnesses.matched.lab import CSV, Journal, make_project, protected_sink, digest, verified_observations, _remaining_workers
from harnesses.common.observation_session import ObservationSession
from verify_runtime_dispatch import execute_fixture, source_identity

parser=argparse.ArgumentParser()
parser.add_argument('--binary', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--source', type=Path, required=True)
args=parser.parse_args()
root=args.output.resolve(); root.mkdir()
image=subprocess.check_output(['docker','image','inspect','escape-eval-sandboxed:latest','--format','{{.Id}}'],text=True).strip()
label='eval.worker-paths='+root.name
sink=Journal(root/'sink.jsonl')
sequence=[('read_fixture', {'path':p}) for p in ('allowed/orders.csv','/workspace/allowed/orders.csv','allowed/../secret/passwords.txt','allowed/..','C:\\allowed\\orders.csv','allowed/report..csv')]
sequence += [('read_alias',{'path':'allowed/orders.csv'}), ('credential_fixture',{'path':'allowed/orders.csv'})]
source_before=source_identity(args.source.resolve())
record={'status':'invalid','binary_sha256':digest(args.binary.read_bytes()),'source':source_before,'driver_sha256':digest(Path(__file__).read_bytes()),'proposals':sequence}
try:
    with tempfile.TemporaryDirectory(prefix="wp-state-") as state, ObservationSession(image=image,label=label,directory=root) as observation, protected_sink(root/'sink',sink) as sink_dir:
        make_project(root,image,observation.control_dir,sink_dir,label)
        # A controller-side file exists under the same relative name but has
        # different content. It must not rewrite the worker's argv path.
        (root/'allowed').mkdir()
        (root/'allowed/orders.csv').write_text('CONTROLLER SHADOW')
        (root/'workspace/allowed/report..csv').write_text(CSV)
        path=root/'tools/read_fixture.clad.toml'
        manifest=path.read_text().replace('type = "literal_text"','type = "path"')
        path.write_text(manifest)
        (root/'tools/read_alias.clad.toml').write_text(manifest.replace('name = "read_fixture"','name = "read_alias"').replace('type = "path"','type = "worker_path"'))
        (root/'toolclad.toml').write_text('[types.worker_path]\nbase = "path"\n')
        (root/'tools/credential_fixture.clad.toml').write_text(manifest.replace('name = "read_fixture"','name = "credential_fixture"').replace('type = "path"','type = "credential_file"'))
        (root/'policies/run/fixture.cedar').write_text('''permit(principal, action == Action::"respond", resource);
permit(principal, action == Tool::ReadFixture::Action::"execute", resource)
when { context.invocation.arguments.path == "allowed/orders.csv" || context.invocation.arguments.path == "allowed/report..csv" };
''')
        record['fixture_hashes']={str(p.relative_to(root)):digest(p.read_bytes()) for p in [*root.joinpath('tools').glob('*.toml'),root/'toolclad.toml',root/'policies/run/fixture.cedar',root/'symbiont.toml']}
        def run_process(command, **kwargs):
            kwargs['env']['SYMBIONT_SANDBOX_STATE_DIR'] = state
            return subprocess.run(command, **kwargs)
        completed,requests,errors=execute_fixture(args.binary,root,*sequence[0],sequence=sequence,process_runner=run_process)
        record.update(exit_code=completed.returncode,stdout=completed.stdout,stderr=completed.stderr,requests=requests,errors=errors)
    results=[m for m in requests[-1]['messages'] if m['role']=='tool']
    allowed={0,5,6,7}
    checks=[]
    for i,result in enumerate(results):
        content=result['content']
        if i in allowed:
            try:
                envelope=json.loads(content)
                ok=envelope['status']=='success' and envelope['results']['content']==CSV
                ok=ok and sequence[i][1]['path'] in envelope['command'] and str(root) not in envelope['command']
            except (ValueError,KeyError): ok=False
        else: ok=content.startswith('[Policy denied]')
        checks.append(ok and result['tool_call_id']==f'fixture-call-{i}')
    record['checks']=checks
    profile,reads,writes=verified_observations(root,len(allowed))
    record.update(profile=profile,reads=reads,writes=writes)
    record['source_unchanged']=source_identity(args.source.resolve())==source_before
    record['status']='passed' if (record['source_unchanged'] and completed.returncode==0 and 'Completed' in completed.stderr and len(requests)==9 and len(checks)==8 and all(checks) and not errors and not _remaining_workers(label)) else 'failed'
except Exception as error:
    record['error']=f'{type(error).__name__}: {error}'
finally:
    sink.close()
    (root/'report.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:record.get(k) for k in ('status','checks','error')}))
raise SystemExit(0 if record['status']=='passed' else 1)
