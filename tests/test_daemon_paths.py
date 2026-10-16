"""Invalid local identities must be rejected before filesystem construction."""

import pytest

from scripts import reflexd


@pytest.mark.parametrize("name", ["../../../escape-claude", "/tmp/escape-codex", "a/../b-claude"])
def test_daemon_rejects_path_participants(tmp_path, monkeypatch, name):
    writes = []
    monkeypatch.setattr(reflexd.reflexd_wake, "write_agent_config_dir",
                        lambda root, **kwargs: writes.append(root))
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="test-key", participant_name=name,
        runtime_dir=tmp_path / "runtime", log_path=tmp_path / "wake.log",
    )
    with pytest.raises(ValueError, match="participant"):
        reflexd.Reflexd(cfg)
    assert writes == []
