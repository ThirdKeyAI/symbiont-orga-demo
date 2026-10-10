#!/usr/bin/env python3
"""Managed source restrictions and exact admission through the shipping CLI."""
import json
from pathlib import Path
import re

import verify_runtime_dispatch as common
import verify_runtime_managed_cli as native
import verify_runtime_terminal as terminal

CASES = [(name,) for name in (
    'global_allowed', 'selected_allowed', 'admission_approved', 'source_changed_during_review',
    'admission_inline_denied', 'admission_missing_relay', 'admission_review_denied',
    'admission_wrong_id', 'admission_expired', 'broker_inline_denied',
    'unsupported_global_rule', 'reserved_tool', 'invalid_approval_metadata',
)]
REVIEW = {'admission_approved':'approve', 'source_changed_during_review':'approve',
          'admission_review_denied':'deny', 'admission_wrong_id':'wrong_id', 'admission_expired':'wait'}
PREFLIGHT = {'unsupported_global_rule':'Unsupported agent policy', 'reserved_tool':'reserved for managed worker admission',
             'invalid_approval_metadata':'human_approval metadata must be true or false'}
ADMISSION_DENIED = {'admission_inline_denied','admission_missing_relay','admission_review_denied','admission_wrong_id','admission_expired'}


def run_case(binary, case, image, *, source):
    name = case[0]
    operator = terminal.TerminalOperator([REVIEW[name]] if name in REVIEW else [])
    trusted = {}
    base = 'policy_denied' if name == 'broker_inline_denied' else 'allowed_write' if name in REVIEW else 'allowed_source_git'
    expected = PREFLIGHT.get(name) or ('policy gate denied claude_code spawn' if name in ADMISSION_DENIED else None)

    def setup(root):
        trusted['public'] = terminal.provision_key(root)
        agent_path = root/'agents/fixture.symbi'
        agent = agent_path.read_text()
        required = name in REVIEW or name in {'admission_inline_denied','admission_missing_relay'}
        if required:
            agent = agent.replace('metadata {', 'metadata { human_approval = true', 1)
        if name == 'invalid_approval_metadata':
            agent = agent.replace('metadata {', 'metadata { human_approval = "yes"', 1)
        names = ['claude_code', *native.TOOLS, 'write_fixture']
        global_policy = 'policy global_scope { allow: ' + json.dumps(names) + ' }\n'
        if name == 'admission_inline_denied':
            global_policy = 'policy global_scope { deny: "claude_code" }\n'
        if name == 'unsupported_global_rule':
            global_policy = 'policy global_scope { require: context.verified }\n'
        if name == 'selected_allowed':
            agent = agent.replace('agent fixture(input: String) -> String {',
                'agent fixture(input: String) -> String { policy selected_scope { allow: ' + json.dumps(names) + ' }')
            agent += '\nagent sibling() { policy sibling_scope { deny: true } }\n'
        if name == 'broker_inline_denied':
            global_policy = 'policy global_scope { deny: "read_file" if invocation.arguments.path == "secret.txt" }\n'
            manifest = root/'tools/read_file.clad.toml'
            contents = manifest.read_text()
            if 'human_approval = false' in contents:
                contents = contents.replace('human_approval = false','human_approval = true')
            else:
                contents = contents.replace('[tool]', '[tool]\nhuman_approval = true',1)
            manifest.write_text(contents)
        agent = global_policy + agent
        agent_path.write_text(agent)
        source_hash = common.sha256(json.dumps(agent, ensure_ascii=False, separators=(',',':')).encode())
        trusted.update(source_hash=source_hash, agent=agent)
        policy = (root/'policies/managed-cli/fixture.cedar')
        policy.write_text('permit(principal, action, resource) when { context.source_policy.agent_name == "fixture" && context.invocation.source_policy.source_hash == context.source_policy.source_hash && context.source_policy.source_hash == '+json.dumps(source_hash)+' };\n')
        if name == 'reserved_tool':
            manifest = (root/'tools/write_fixture.clad.toml').read_text().replace('name = "write_fixture"','name = "claude_code"')
            (root/'tools/reserved.clad.toml').write_text(manifest)

    def runner(command, **kwargs):
        root = Path(kwargs['cwd'])
        def before_response():
            assert not (root/'source/result').exists()
            if operator.requests and operator.requests[-1]['context_snapshot']['invocation']['contract']['name'] == 'claude_code':
                journals = list((root/'.symbiont/governed').glob('*.jsonl'))
                entries = native.verify_journal(journals[0], trusted['public'])
                assert not any('InferenceRequested' in entry['event'] for entry in entries)
                if name == 'source_changed_during_review':
                    (root/'agents/fixture.symbi').write_text('policy replaced { deny: true }\nagent fixture() {}\n')
                    profile = root/'symbiont.toml'
                    profile.write_text(profile.read_text().replace(image, 'missing-worker-after-review:local'))
                    manifest = root/'tools/write_fixture.clad.toml'
                    manifest.write_text(manifest.read_text().replace('SYMBIONT_E2E_SOURCE_', 'REPLACED_'))
                    trusted['mutated'] = True
            return False
        operator.effect_probe = before_response
        return operator(command, **kwargs)

    def evidence(root, completed, entries):
        assert len(operator.requests) == (1 if name in REVIEW else 0)
        if name in PREFLIGHT:
            assert not entries and not list((root/'.symbiont/governed').glob('*.jsonl'))
            return dict(passed=True, refused_before_journal=True)
        public = re.search(r'(?m)^  audit public key: ([0-9a-f]{64})$', completed.stdout)
        assert public and public[1] == trusted['public']
        journals = list((root/'.symbiont/governed').glob('*.jsonl'))
        assert len(journals) == 1 and native.verify_journal(journals[0], trusted['public']) == entries
        assert entries[0]['event']['Started']['execution_context']['source_policy']['source_hash'] == trusted['source_hash']
        assert entries[0]['event']['Started']['execution_context']['agent_name'] == 'fixture'
        failed = name in ADMISSION_DENIED
        assert (entries[-1]['event']['Terminated']['reason'] != 'Completed') == failed
        approved = terminal.verify_approval(entries, operator)
        assert len(approved) == (1 if REVIEW.get(name) == 'approve' else 0)
        denied = [call for entry in entries for call in entry['event'].get('PolicyEvaluated', {}).get('denied_calls', [])]
        if failed:
            assert not native.admission_evidence(entries)['pre_effect']
            assert not any('InferenceRequested' in entry['event'] for entry in entries)
            assert len(denied) == 1
            expected_reason = {'admission_inline_denied':'inline policy global_scope',
                'admission_missing_relay':'approval relay is unavailable','admission_expired':'timeout'}.get(name,'terminal approval denied')
            assert expected_reason in denied[0]['reason'], denied
        if name == 'broker_inline_denied':
            assert len(denied) == 1 and denied[0]['reason'] == 'inline policy global_scope rule 1 denied effect'
        for held in operator.requests:
            invocation = held['context_snapshot']['invocation']
            assert held['agent_id'] == entries[0]['agent_id']
            assert invocation['source_policy']['source_hash'] == trusted['source_hash']
            assert invocation['contract']['name'] == 'claude_code'
            args = invocation['arguments']
            assert args['request']['prompt'] == 'Complete the scripted registered tool exchange.'
            assert args['argv'][0] == 'python3' and args['argv'][1] == '/opt/symbi-broker/inference_bridge.py'
            assert args['sandbox']['container']['network_mode'] == 'none'
            assert args['sandbox']['container']['working_dir'] == '/workspace'
            assert len(args['sandbox']['container']['mounts']) == 1
            assert str(root/'source') not in str(args['sandbox']['container']['mounts'])
            assert str(root/'source') in str(args['tool_sandbox']['container']['mounts'])
            assert native.KEY not in json.dumps(args)
        if name == 'source_changed_during_review':
            assert trusted.get('mutated') is True
            assert (root/'agents/fixture.symbi').read_text() != trusted['agent']
        return dict(passed=True, public_key=trusted['public'], source_hash=trusted['source_hash'],
            operator_requests=operator.requests, operator_sent=operator.sent,
            terminal_digest=common.sha256(operator.transcript), approved_calls=approved,
            source_mutated=trusted.get('mutated',False))

    use_terminal = name != 'admission_missing_relay'
    options = ['--approval-terminal','--approval-timeout','1' if name == 'admission_expired' else '10'] if use_terminal else []
    record = native.run_case(binary, (base,), image, source=source, fixture_setup=setup,
        process_runner=runner if use_terminal else None, command_options=options,
        evidence_verifier=evidence, expected_prelaunch=expected,
        denial_text='inline policy global_scope rule 1 denied effect' if name == 'broker_inline_denied' else None)
    record['case'] = name
    return record


if __name__ == '__main__':
    raise SystemExit(common.main(cases=CASES,
        case_factory=lambda source: lambda binary, case, image: run_case(binary,case,image,source=source),
        image_reference='symbi-managed-real-e2e:local', companion_driver=Path(__file__),
        additional_drivers=[Path(native.__file__),Path(terminal.__file__)], suite='shipping-managed-source-admission'))
