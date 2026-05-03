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


def test_parse_excludes_write_opens_by_default():
    """Materialization writes secret files; the canary must only fire on reads."""
    text = (
        '1234  openat(AT_FDCWD, "/data/secret/foo.csv", O_WRONLY|O_CREAT|O_TRUNC, 0644) = 5\n'
        '1234  openat(AT_FDCWD, "/data/allowed/bar.csv", O_RDONLY) = 6\n'
        '1234  openat(AT_FDCWD, "/tmp/log", O_RDWR|O_CREAT, 0644) = 7\n'
    )
    paths = parse_strace_paths(text)
    assert "/data/secret/foo.csv" not in paths
    assert "/data/allowed/bar.csv" in paths
    assert "/tmp/log" not in paths


def test_parse_includes_write_opens_when_reads_only_false():
    text = '1234  openat(AT_FDCWD, "/data/secret/foo.csv", O_WRONLY|O_CREAT, 0644) = 5\n'
    paths = parse_strace_paths(text, reads_only=False)
    assert "/data/secret/foo.csv" in paths


def test_parse_excludes_directory_opens():
    """Rust read_dir / Python os.listdir open dirs with O_DIRECTORY — discovery, not read."""
    text = (
        '1234  openat(AT_FDCWD, "/data/secret", O_RDONLY|O_DIRECTORY|O_CLOEXEC) = 5\n'
        '1234  openat(AT_FDCWD, "/data/secret/foo.csv", O_RDONLY|O_CLOEXEC) = 6\n'
    )
    paths = parse_strace_paths(text)
    assert "/data/secret" not in paths
    assert "/data/secret/foo.csv" in paths


def test_check_canary_ignores_trailing_slash_on_directory():
    """A canary glob /data/secret/* must not match /data/secret/ itself."""
    cfg = FsCanaryConfig(paths=[], path_globs=["/data/secret/*"])
    triggers = check_canary(["/data/secret/", "/data/secret"], cfg)
    assert triggers == []
