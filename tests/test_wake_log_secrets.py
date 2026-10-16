"""Prove wake logs never persist stream credentials or raw harness stderr."""

import logging
from types import SimpleNamespace

import pytest

from scripts import reflexd


@pytest.mark.parametrize("debug", [False, True])
def test_stream_tokens_are_redacted_at_log_sink(tmp_path, monkeypatch, capsys, debug):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", root.level)
    path = tmp_path / "wake.log"
    reflexd.configure_logging(path, debug=debug)
    try:
        url = "http://relay/stream/dm/alice?token=PRIVATE-stream-token&other=ok"
        logging.getLogger("httpx").warning("HTTP request %s", url)
        try:
            raise ValueError(f"stream failed: {url}")
        except ValueError:
            reflexd.logger.exception("stream failure")
        for handler in root.handlers:
            handler.flush()
        output = path.read_text() + capsys.readouterr().err
        assert "PRIVATE-stream-token" not in output
        assert "other=ok" in output
        assert "ValueError" in output
    finally:
        for handler in root.handlers:
            handler.close()


async def test_harness_stderr_is_not_logged(monkeypatch, caplog):
    async def communicate():
        return b"", b"Invalid API key PRIVATE-api-credential"

    async def spawn(*args, **kwargs):
        return SimpleNamespace(returncode=1, communicate=communicate)

    monkeypatch.setattr(reflexd.shutil, "which", lambda _: "/fake/claude")
    monkeypatch.setattr(reflexd.asyncio, "create_subprocess_exec", spawn)
    with caplog.at_level(logging.WARNING):
        result = await reflexd.HeadlessAdapter()._run_subprocess(
            ["claude"], parser=lambda out: out,
        )
    assert "PRIVATE-api-credential" not in caplog.text
    assert "PRIVATE-api-credential" not in result
    assert "exited 1" in caplog.text
