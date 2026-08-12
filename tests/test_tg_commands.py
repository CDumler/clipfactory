import sys
import types
import unittest
from unittest.mock import Mock, patch

_requests_stub = types.ModuleType("requests")
_requests_stub.get = None
_requests_stub.post = None
sys.modules.setdefault("requests", _requests_stub)

from app import tg


class TelegramCommandDispatchTest(unittest.TestCase):
    def test_clip_without_name_opens_menu_handler(self):
        handlers = {
            "on_clip_menu": Mock(),
            "on_single_clip": Mock(),
        }
        with patch("app.tg.send") as send:
            handled = tg._dispatch_text_message("/clip", 123, handlers)

        self.assertTrue(handled)
        handlers["on_clip_menu"].assert_called_once_with(123)
        handlers["on_single_clip"].assert_not_called()
        send.assert_not_called()

    def test_slash_command_and_plain_text_are_separated(self):
        handlers = {
            "on_status": Mock(),
            "on_text": Mock(),
        }
        with patch("app.tg.send") as send:
            handled_cmd = tg._dispatch_text_message("/status", 123, handlers)
            handled_text = tg._dispatch_text_message("bitte kuerzer", 123, handlers)

        self.assertTrue(handled_cmd)
        self.assertTrue(handled_text)
        handlers["on_status"].assert_called_once_with(123)
        handlers["on_text"].assert_called_once_with("bitte kuerzer", 123)
        send.assert_not_called()

    def test_unknown_command_returns_help_hint(self):
        handlers = {}
        with patch("app.tg.send") as send:
            handled = tg._dispatch_text_message("/doesnotexist", 123, handlers)

        self.assertTrue(handled)
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
