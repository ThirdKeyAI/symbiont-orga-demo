from detectors.state_detector import diff, hash_file, snapshot


def test_unchanged_file_no_trigger(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text('{"allowed_actions": ["read"]}')
    before = snapshot([p])
    after = snapshot([p])
    assert diff(before, after) == []


def test_modified_file_fires_trigger(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text('{"allowed_actions": ["read"]}')
    before = snapshot([p])
    p.write_text('{"allowed_actions": ["read", "execute"]}')
    after = snapshot([p])
    triggers = diff(before, after)
    assert len(triggers) == 1
    assert triggers[0]["vector"] == "state"


def test_deleted_file_fires_trigger(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text('x')
    before = snapshot([p])
    p.unlink()
    after = snapshot([p])
    assert len(diff(before, after)) == 1


def test_hash_file_returns_none_for_missing(tmp_path):
    assert hash_file(tmp_path / "nope") is None
