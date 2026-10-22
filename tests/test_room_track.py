"""Board and CLI show only one owner's repo-local aggregates."""
import argparse
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from quorus_cli.track_cmd import cmd_track
from rich.console import Console

from quorus.auth.middleware import AuthContext
from quorus.routes import room_track
from quorus.runtime import task_display, task_record


@pytest.mark.asyncio
async def test_route_limits_tenant_and_owner_before_reading_repo(monkeypatch):
    async def member(*args):
        return "r", {"name": "room"}
    async def owner(*args):
        return "alice"
    monkeypatch.setattr(room_track, "require_room_member", member)
    monkeypatch.setattr(room_track, "agent_owner", owner)
    monkeypatch.setattr(room_track, "bound_repo", lambda _: pytest.fail("local repo leaked"))
    request = SimpleNamespace()
    with pytest.raises(HTTPException) as error:
        await room_track.room_track("r", request, "eve", AuthContext(sub="a", tenant_id="t"))
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await room_track.room_track("r", request, "alice", AuthContext(sub="a", tenant_id="t"))
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_route_returns_aggregates_only_for_scoped_binding(monkeypatch, tmp_path):
    async def member(*args):
        return "r", {"name": "room"}
    monkeypatch.setattr(room_track, "require_room_member", member)
    monkeypatch.setattr(room_track, "bound_repo", lambda _: tmp_path)
    monkeypatch.setattr(room_track, "load_config", lambda: {"relay_url": "http://local"})
    binding = tmp_path / "bindings.json"
    binding.write_text(json.dumps({"room": {"main": "integration"}}))
    monkeypatch.setattr(room_track, "bindings_path", lambda: binding)
    def report(repo, main, scope):
        assert repo == tmp_path and main == "integration"
        assert scope == task_record.scope("http://local", "alice", tmp_path)
        return []
    monkeypatch.setattr(task_record, "report", report)
    result = await room_track.room_track("r", SimpleNamespace(), "alice",
                                          AuthContext(is_legacy=True))
    assert result == {"label": task_display.LABEL, "days": 30, "cells": []}
    assert str(tmp_path) not in json.dumps(result)


def test_cli_scope_and_empty_label(monkeypatch, tmp_path):
    from quorus_cli import track_cmd
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(track_cmd, "load_config", lambda: {"instance_name": "alice",
                                                          "relay_url": "http://local"})
    def report(repo, main, scope):
        assert scope == task_record.scope("http://local", "alice", tmp_path)
        assert main == "main"
        return []
    monkeypatch.setattr(task_record, "report", report)
    output = io.StringIO()
    cmd_track(argparse.Namespace(repo=str(tmp_path), json=True), Console(file=output, width=200))
    assert json.loads(output.getvalue())["label"] == task_display.LABEL


@pytest.mark.parametrize("instance", ["alice-smith", "alice-smith-codex",
                                      "alice-smith-codex-desktop"])
def test_cli_preserves_dashed_owner_scope(monkeypatch, tmp_path, instance):
    from quorus_cli import track_cmd
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(track_cmd, "load_config", lambda: {"instance_name": instance,
                                                          "relay_url": "http://local"})
    def report(repo, main, scope):
        assert scope == task_record.scope("http://local", "alice-smith", tmp_path)
        return []
    monkeypatch.setattr(task_record, "report", report)
    cmd_track(argparse.Namespace(repo=str(tmp_path)), Console(file=io.StringIO()))
