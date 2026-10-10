"""Terminal-only approval evidence must include the matching API resolution."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import verify_runtime_tui as tui


def test_inert_binary_cannot_pass_the_shipping_tui_trial():
    result=tui.run_case(Path('/usr/bin/true'), ('approved',), 'unused')
    assert not result['valid'] and not result['passed']
    assert not result['posts']


def test_display_claims_cannot_rescue_missing_resolution(monkeypatch):
    class DisplayOnly:
        def __init__(self, *_):
            self.frames=['claimed approval display']
            self.server_pid=None
            self.pane_pid=None
        def shown(self, *_args, **_kwargs): pass
        def send(self, *_args): pass
        def details(self, *_args): pass
        def wait(self, predicate, *_args, **_kwargs):
            assert predicate(), 'no actual approval request reached the server'
        def close(self): return True
    monkeypatch.setattr(tui, 'TerminalShell', DisplayOnly)
    result=tui.run_case(Path('/usr/bin/true'), ('approved',), 'unused')
    assert not result['valid'] and not result['passed']
    assert not result['posts']
    assert 'no actual approval request' in result['error']


def test_terminal_capture_preserves_unicode(tmp_path):
    try:
        tui.terminal_binary()
    except RuntimeError:
        pytest.skip('tmux terminal fixture is unavailable')
    shell=tui.TerminalShell(Path('/usr/bin/cat'),tmp_path)
    try:
        shell.send('TERMINAL · ⏳ probe\r')
        shell.shown('TERMINAL · ⏳ probe')
    finally:
        assert shell.close()
