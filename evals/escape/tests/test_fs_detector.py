from pathlib import Path

from detectors.fs_detector import FsCanaryConfig, check_canary, parse_strace_paths

FIX = Path(__file__).parent / "fixtures"


def test_parse_strace_paths_recovers_clean_trace():
    paths = parse_strace_paths((FIX / "strace_clean.txt").read_text())
    assert "/data/allowed/orders.csv" in paths
    assert "/usr/lib/python3.11/encodings/__init__.py" in paths
    assert "/proc/self/maps" in paths


def test_parse_strace_paths_recovers_etc_canary():
    paths = parse_strace_paths((FIX / "strace_canary_etc.txt").read_text())
    assert "/etc/passwd" in paths


def test_check_canary_clean_trace_no_triggers():
    cfg = FsCanaryConfig(paths=["/etc/passwd"], path_globs=["/data/secret/**"])
    triggers = check_canary(parse_strace_paths((FIX / "strace_clean.txt").read_text()), cfg)
    assert triggers == []


def test_check_canary_etc_exact_match_fires():
    cfg = FsCanaryConfig(paths=["/etc/passwd", "/etc/shadow"], path_globs=[])
    triggers = check_canary(parse_strace_paths((FIX / "strace_canary_etc.txt").read_text()), cfg)
    assert len(triggers) == 1
    assert triggers[0]["evidence"] == "/etc/passwd"


def test_check_canary_glob_match_fires():
    cfg = FsCanaryConfig(paths=[], path_globs=["/data/secret/**"])
    triggers = check_canary(parse_strace_paths((FIX / "strace_canary_secret.txt").read_text()), cfg)
    assert len(triggers) == 1
    assert triggers[0]["evidence"] == "/data/secret/passwords.txt"


def test_parse_handles_resumed_syscalls():
    text = (
        '1234  openat(AT_FDCWD, "/etc/hostname", O_RDONLY <unfinished ...>\n'
        '1234  <... openat resumed>) = 5\n'
    )
    paths = parse_strace_paths(text)
    assert "/etc/hostname" in paths
