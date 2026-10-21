"""Checkpoints bind actual failures to a clean immutable tree, never a prompt."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from quorus.runtime.check_config import CheckConfig, configure_binding
from quorus.runtime.checkpoint import run_checkpoint


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path).decode().strip()
    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "tracked").write_text("initial")
    git("add", "tracked")
    git("commit", "-qm", "initial")
    return tmp_path, git("rev-parse", "HEAD")


def run(repo, source, **kw):
    root, head = repo
    return run_checkpoint(root, head, CheckConfig((sys.executable, "-c", source), **kw),
                          env={"PATH": os.defpath})


def test_actual_failure_records_stderr_and_redacts_before_tail(repo):
    secret = "quorus_pk_" + "x" * 24
    record = run(repo, f"import sys; print('{secret}',file=sys.stderr); sys.exit(1)")
    assert record["status"] == "ran" and record["exit"] == 1
    assert secret not in record["tail"] and "[redacted]" in record["tail"]
    assert record["head"] == repo[1] and len(record["tree"]) == 40


@pytest.mark.parametrize("source,status", [
    ("print('ok')", "passing"),
    ("import time; time.sleep(2)", "timed_out"),
    ("import os,signal; os.kill(os.getpid(),signal.SIGTERM)", "error"),
])
def test_only_completed_failure_qualifies(repo, source, status):
    record = run(repo, source, timeout_s=0.2, max_output_bytes=1024)
    assert record["status"] == status
    if status != "passing":
        assert record["exit"] is None


@pytest.mark.parametrize("source", [
    "from pathlib import Path; Path('tracked').write_text('changed'); raise SystemExit(1)",
    "from pathlib import Path; Path('new').write_text('artifact'); raise SystemExit(1)",
    "import subprocess; subprocess.run(['git','add','tracked']); "
    "subprocess.run(['git','commit','--allow-empty','-qm','changed']); raise SystemExit(1)",
])
def test_post_run_changes_never_become_proof(repo, source):
    assert run(repo, source)["status"] == "modified_tree"


def test_dirty_tree_and_wrong_head_do_not_execute(repo):
    root, head = repo
    (root / "tracked").write_text("work in progress")
    assert run(repo, "raise RuntimeError('must not run')")["status"] == "skipped"
    assert (root / "tracked").read_text() == "work in progress"
    assert run_checkpoint(root, "0" * 40, None, env={})["status"] == "none"


def test_cwd_symlink_cannot_escape_repo(repo, tmp_path):
    root, head = repo
    # Ignore the link so the cwd validation, rather than dirty-tree gating, is exercised.
    (root / ".git" / "info" / "exclude").write_text("outside\n")
    (root / "outside").symlink_to(root.parent, target_is_directory=True)
    config = CheckConfig((sys.executable, "-c", "raise RuntimeError"), cwd="outside")
    assert run_checkpoint(root, head, config, env={})["status"] == "error"


def test_truncated_output_cannot_publish_partial_credentials(repo):
    result = run(repo, "print('prefix-' + 'x'*20000)", max_output_bytes=1024)
    assert result["status"] == "passing" and result["tail"] == ""
    assert result["truncated"] is True


def test_large_failing_output_keeps_failure_at_end_and_actual_exit(repo):
    record = run(repo, "import sys; print('noise\\n'*10000,flush=True); "
                 "print('FAILED test_example',file=sys.stderr); sys.exit(7)", max_output_bytes=1024)
    assert record["status"] == "ran" and record["exit"] == 7
    assert record["truncated"] and record["tail"].endswith("FAILED test_example")


def test_continuous_output_timeout_keeps_bounded_partial_tail(repo):
    record = run(repo, "import os\nwhile True: os.write(1,b'whole line\\n'*100)",
                 timeout_s=.2, max_output_bytes=1024)
    assert record["status"] == "timed_out" and record["exit"] is None
    assert record["truncated"] and len(record["tail"].encode()) <= 1024
    assert set(record["tail"].splitlines()) == {"whole line"}


def test_timeout_kills_descendants(repo):
    import time

    root, _ = repo
    child = "import time; from pathlib import Path; time.sleep(.6); Path('leaked').touch()"
    source = (f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]);"
              "time.sleep(5)")
    assert run(repo, source, timeout_s=.2)["status"] == "timed_out"
    time.sleep(.7)
    assert not (root / "leaked").exists()


def test_config_roundtrip_and_explicit_revocation(tmp_path):
    args = SimpleNamespace(check="python -m pytest 'tests/a b.py'", check_timeout=30,
                           check_output_kb=8)
    entry = configure_binding(str(tmp_path), None, args, tmp_path)
    config = CheckConfig.from_record(entry["check"])
    assert config.argv[-1] == "tests/a b.py"
    assert CheckConfig.from_record(config.record()).config_id == config.config_id
    assert CheckConfig(config.argv, timeout_s=31).config_id != config.config_id
    no_change = SimpleNamespace(check=None, check_timeout=None, check_output_kb=None)
    assert configure_binding(entry, entry, no_change, tmp_path)["check"] == config.record()
    moved = configure_binding({**entry, "path": str(tmp_path / "other")}, entry,
                              no_change, tmp_path / "other")
    assert "check" not in moved
    assert "check" not in configure_binding(entry, entry, SimpleNamespace(check=""), tmp_path)


@pytest.mark.parametrize("kwargs", [
    {"argv": ()}, {"argv": ("pytest\0",)}, {"cwd": "../elsewhere"},
    {"timeout_s": float("nan")}, {"timeout_s": True}, {"timeout_s": 301},
    {"max_output_bytes": True}, {"max_output_bytes": 1048577},
])
def test_invalid_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        CheckConfig(**{"argv": ("pytest",), **kwargs})


def test_cli_bind_stores_check_and_invalid_update_preserves_file(tmp_path, monkeypatch):
    import json

    from quorus_cli import cli

    from quorus.runtime.bindings import bindings_path

    args = SimpleNamespace(room_action="bind", room="checkpoint", path=str(tmp_path),
                           check="python -m pytest", check_timeout=5, check_output_kb=4)
    cli._cmd_room(args)
    path = bindings_path()
    before = path.read_bytes()
    assert json.loads(before)["checkpoint"]["check"]["timeout_s"] == 5
    args.check_timeout = float("inf")
    with pytest.raises(SystemExit) as error:
        cli._cmd_room(args)
    assert error.value.code == 2 and path.read_bytes() == before


def test_only_pytests_summary_timing_is_dropped():
    """Codex on 3e0f4351: dropping every "in Ns" made "expected retry in 1s" and
    "in 2s" hash the same, so two different failures could read `reproduced`."""
    from quorus.runtime.checkpoint import without_timings

    one, two = (without_timings(f"E   AssertionError: expected retry in {n}s\n"
                                "1 failed, 1 passed in 0.03s\n") for n in (1, 2))
    assert one != two and "retry in 1s" in one and "1 failed, 1 passed in <t>s" in one
    for line, want in (
            ("==== 5 failed, 4 passed, 2 warnings in 12.50s (0:00:12) ====",
             "==== 5 failed, 4 passed, 2 warnings in <t>s ===="),
            ("1 error in 0.01s", "1 error in <t>s"),
            ("no tests ran in 0.00s", "no tests ran in <t>s"),
            ("3 passed in 0.02s and more", "3 passed in 0.02s and more"),  # not a summary
            ("    1 failed in 2s", "    1 failed in 2s")):  # indented: part of output
        assert without_timings(line) == want, line
