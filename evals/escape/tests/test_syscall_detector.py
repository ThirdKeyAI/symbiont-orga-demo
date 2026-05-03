from detectors.syscall_detector import check_canary, parse_execves


def test_parse_single_execve():
    text = '1234  execve("/usr/bin/python3", ["python3", "-m", "harness"], 0x...) = 0\n'
    out = parse_execves(text)
    assert len(out) == 1
    assert out[0][0] == "/usr/bin/python3"


def test_baseline_subtracted():
    text = (
        '1234  execve("/usr/bin/python3", ["python3", "-m", "harness"], 0x...) = 0\n'
        '5678  execve("/bin/sh", ["sh", "-c", "ls"], 0x...) = 0\n'
        '5679  execve("/bin/ls", ["ls"], 0x...) = 0\n'
    )
    triggers = check_canary(parse_execves(text), baseline_count=1)
    assert len(triggers) == 2
    assert "execve /bin/sh" in triggers[0]["evidence"]


def test_no_extra_execves_no_trigger():
    text = '1234  execve("/usr/bin/python3", ["python3"], 0x...) = 0\n'
    triggers = check_canary(parse_execves(text), baseline_count=1)
    assert triggers == []
