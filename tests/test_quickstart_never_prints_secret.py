"""0.7.5 security fix: `quorus quickstart` printed its relay secret ("Secret: …")
in the ready banner, so a pasted or recorded terminal carried it. It now says
where the secret is saved instead."""
import types
from unittest.mock import MagicMock, patch

from quorus_cli import cli

SECRET = "qs-s3cret-value-xyz"


def test_quickstart_prints_where_the_secret_is_not_the_secret(tmp_path, capsys):
    ok = MagicMock(status_code=200)
    ok.json.return_value = {}
    with patch("secrets.token_urlsafe", return_value=SECRET), \
         patch.object(cli.subprocess, "Popen", return_value=MagicMock(pid=4242)), \
         patch("httpx.get", return_value=ok), patch("httpx.post", return_value=ok), \
         patch.object(cli, "_config_dir", return_value=tmp_path), \
         patch.object(cli, "_spawn_agent"), \
         patch.object(cli, "_chat", new=MagicMock(return_value=None)), \
         patch.object(cli.asyncio, "run"):
        cli._cmd_quickstart(types.SimpleNamespace(agents=1))
    out = capsys.readouterr().out
    assert "Quickstart ready" in out
    assert SECRET not in out
    flat = "".join(out.split())  # rich wraps the long path
    assert "relay_secret" in flat and str(tmp_path / "config.json") in flat
    # It is still saved where the banner says, for the user's own tools.
    assert SECRET in (tmp_path / "config.json").read_text()
