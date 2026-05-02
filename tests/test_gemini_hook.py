import json
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
        # Use a temporary cursors file path for testing
        self.patcher_home = patch("quorus_cli.hooks.Path.home", return_value=Path("/tmp"))
        self.mock_home = self.patcher_home.start()

        # Ensure the test directory exists
        (Path("/tmp") / ".quorus").mkdir(parents=True, exist_ok=True)

        self.cursors_file = _cursors_path(self.agent)
        if self.cursors_file.exists():
            self.cursors_file.unlink()

    def tearDown(self):
        self.patcher_home.stop()
        if self.cursors_file.exists():
            self.cursors_file.unlink()

    @patch("quorus_cli.hooks._config")
    @patch("quorus_cli.hooks._fetch_unread")
    @patch("sys.stdout")
    def test_handle_gemini_beforeagent_json_shape(self, mock_stdout, mock_fetch, mock_config):
        # (a) handle_gemini_beforeagent JSON shape
        mock_config.return_value = ("http://relay", "secret", self.agent)

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
        # (b) cursor advance per room
        cursors = {"room1": "msg1", "room2": "msg1"}

        # First batch: new message for room1
        messages1 = [
            {"id": "msg2", "room": "room1", "content": "new room1"}
        ]
        fresh1 = _filter_unseen(messages1, cursors)
        self.assertEqual(len(fresh1), 1)
        self.assertEqual(cursors["room1"], "msg2")
        self.assertEqual(cursors["room2"], "msg1")

        # Second batch: same message for room1 (should be filtered)
        fresh2 = _filter_unseen(messages1, cursors)
        self.assertEqual(len(fresh2), 0)

        # Third batch: new message for room2
        messages3 = [
            {"id": "msg2", "room": "room2", "content": "new room2"}
        ]
        fresh3 = _filter_unseen(messages3, cursors)
        self.assertEqual(len(fresh3), 1)
        self.assertEqual(cursors["room2"], "msg2")

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
