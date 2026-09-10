"""Unavailable/inert binaries cannot establish VM effects or policy enforcement."""
import importlib.util
from pathlib import Path
import sys
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('firecracker_fixture',SCRIPTS/'verify_runtime_firecracker.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('binary,case',[
    ('/usr/bin/true','normalized_allowed'),('/usr/bin/true','literal_allowed'),
    ('/usr/bin/true','guest_isolation'),('/usr/bin/true','parser_allowed'),
    ('/usr/bin/true','missing_init'),('/usr/bin/false','policy_denied'),
    ('/usr/bin/false','approval_missing'),('/usr/bin/false','deadline'),
    ('/usr/bin/true','mcp_allowed'),('/usr/bin/true','mcp_unsigned'),
    ('/usr/bin/false','mcp_policy_denied'),('/usr/bin/false','mcp_deadline'),
])
def test_inert_process_is_invalid(binary,case):
    artifacts={name:Path('/unused-artifact') for name in ('kernel','rootfs','firecracker','stale_rootfs')}
    result=module.run_case(Path(binary),case,artifacts)
    assert result['valid'] is False and result['passed'] is False
    assert result['inference_requests']==0 and not result['tool_results']
    assert result['host_canary_intact'] is True
    assert 'error' in result
