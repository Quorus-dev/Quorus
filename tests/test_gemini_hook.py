import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quorus_cli.hooks import (
    _cursors_path,
    _filter_unseen,
    _load_cursors,
    _save_cursors,
    handle_gemini_beforeagent,
)


class TestGeminiHook(unittest.TestCase):
    def setUp(self):
        self.agent = "test-agent"
        self.temporary_home = tempfile.TemporaryDirectory(prefix="gemini-hook-")
        self.home = Path(self.temporary_home.name)
        self.patcher_home = patch("quorus_cli.hooks.Path.home", return_value=self.home)
        self.mock_home = self.patcher_home.start()

        # Ensure the test directory exists
        (self.home / ".quorus").mkdir(parents=True, exist_ok=True)

        self.cursors_file = _cursors_path(self.agent)
        if self.cursors_file.exists():
            self.cursors_file.unlink()

    def tearDown(self):
        self.patcher_home.stop()
        if self.cursors_file.exists():
            self.cursors_file.unlink()
        self.temporary_home.cleanup()

    @patch("quorus_cli.hooks._resolve_auth")
    @patch("quorus_cli.hooks._fetch_unread")
    @patch("sys.stdout")
    def test_handle_gemini_beforeagent_json_shape(self, mock_stdout, mock_fetch, mock_resolve):
        # (a) handle_gemini_beforeagent JSON shape
        mock_resolve.return_value = ("http://relay", self.agent, {"Authorization": "Bearer x"})

        # Test case 1: No messages
        mock_fetch.return_value = []
        handle_gemini_beforeagent()

        # Collect stdout calls
        output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        mock_stdout.write.reset_mock()

        data = json.loads(output.strip())
        self.assertIn("hookSpecificOutput", data)
        self.assertIn("additionalContext", data["hookSpecificOutput"])
        self.assertEqual(data["hookSpecificOutput"]["additionalContext"], "")

        # Test case 2: With messages
        mock_fetch.return_value = [
            {
                "id": "msg1",
                "room": "room1",
                "from_name": "alice",
                "content": "hello",
                "timestamp": "2026-05-02T10:00:00",
            }
        ]
        handle_gemini_beforeagent()

        output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        data = json.loads(output.strip())
        self.assertIn("hookSpecificOutput", data)
        self.assertIn("additionalContext", data["hookSpecificOutput"])
        self.assertIn("hello", data["hookSpecificOutput"]["additionalContext"])
        self.assertIn("@alice", data["hookSpecificOutput"]["additionalContext"])
        self.assertIn("#room1", data["hookSpecificOutput"]["additionalContext"])

    def test_cursor_advance_per_room(self):
        # (b) dedupe by delivery id (chat-list slice 0). The old per-room "last id"
        # compare let every id through except the exact last one; now the hook
        # marks what it showed and never shows that id again, whatever the room.
        from quorus_cli.hooks import _mark_seen

        cursors: dict = {}

        # First batch: new message for room1
        messages1 = [
            {"id": "msg2", "room": "room1", "content": "new room1"}
        ]
        fresh1 = _filter_unseen(messages1, cursors)
        self.assertEqual(len(fresh1), 1)
        _mark_seen(cursors, fresh1)
        self.assertIn("msg2", cursors["_seen"])

        # Second batch: same message for room1 (should be filtered)
        fresh2 = _filter_unseen(messages1, cursors)
        self.assertEqual(len(fresh2), 0)

        # Third batch: a different message for room2 is new
        messages3 = [
            {"id": "msg3", "room": "room2", "content": "new room2"}
        ]
        fresh3 = _filter_unseen(messages3, cursors)
        self.assertEqual(len(fresh3), 1)

    def test_corrupt_cursors_file_fallback(self):
        # (c) corrupt cursors file fallback
        p = self.cursors_file
        p.write_text("THIS IS NOT JSON {[[")

        # _load_cursors should return {} on corruption
        cursors = _load_cursors(self.agent)
        self.assertEqual(cursors, {})

        # Verify it can still be saved correctly after corruption
        new_cursors = {"room1": "msg1"}
        _save_cursors(self.agent, new_cursors)

        loaded = _load_cursors(self.agent)
        self.assertEqual(loaded, new_cursors)


if __name__ == "__main__":
    unittest.main()
