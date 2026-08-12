import os
import sys
import tempfile
import types
import unittest

_requests_stub = types.ModuleType("requests")
_requests_stub.get = None
_requests_stub.post = None
sys.modules.setdefault("requests", _requests_stub)

from app import facecam_detect


class FacecamDetectParseTest(unittest.TestCase):
    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "test-key"
        os.environ["OPENAI_MODEL"] = "gpt-5-mini"

    def test_parse_strips_markdown_fences(self):
        text = """```json
{"found": true, "x": 1400, "y": 50, "w": 420, "h": 240}
```"""
        self.assertEqual(
            facecam_detect.parse_facecam_response(text),
            {"x": 1400, "y": 50, "w": 420, "h": 240},
        )

    def test_parse_rejects_false_and_implausible_boxes(self):
        self.assertIsNone(facecam_detect.parse_facecam_response('{"found": false}'))
        self.assertIsNone(
            facecam_detect.parse_facecam_response(
                '{"found": true, "x": 0, "y": 0, "w": 1920, "h": 1080}'
            )
        )
        self.assertIsNone(
            facecam_detect.parse_facecam_response(
                '{"found": true, "x": 1800, "y": 900, "w": 300, "h": 300}'
            )
        )

    def test_parse_marked_box_strips_markdown_fences(self):
        text = """```json
{"found": true, "x": 1200, "y": 80, "w": 500, "h": 300}
```"""
        self.assertEqual(
            facecam_detect.parse_marked_box_response(text),
            {"x": 1200, "y": 80, "w": 500, "h": 300},
        )

    def test_parse_marked_box_rejects_false_and_too_large(self):
        self.assertIsNone(facecam_detect.parse_marked_box_response('{"found": false}'))
        self.assertIsNone(
            facecam_detect.parse_marked_box_response(
                '{"found": true, "x": 0, "y": 0, "w": 1600, "h": 900}'
            )
        )

    def test_ensure_image_ready_rejects_missing_or_empty_files(self):
        with self.assertRaisesRegex(RuntimeError, "vollständig empfangen"):
            facecam_detect._ensure_image_ready("/tmp/does-not-exist-facecam.jpg")
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaisesRegex(RuntimeError, "vollständig empfangen"):
                facecam_detect._ensure_image_ready(f.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
